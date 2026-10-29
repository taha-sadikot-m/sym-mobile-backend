"""Speech to text with Deepgram. Audio is sent straight through and never stored."""

import logging
import time

import httpx

from app.ai.gemini import AiUnavailable
from app.ai.usage import record
from app.core.config import get_settings

log = logging.getLogger("sym.ai")

ENDPOINT = "https://api.deepgram.com/v1/listen"
MODEL = "nova-3"
TIMEOUT_SECONDS = 60
# Deepgram sometimes answers 408 SLOW_UPLOAD (or 429/5xx) when an upload stalls; a fresh try usually works.
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


async def _post(audio: bytes, content_type: str, key: str) -> httpx.Response:
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(
                    ENDPOINT,
                    params={"model": MODEL, "smart_format": "true", "punctuate": "true", "language": "en"},
                    headers={"Authorization": f"Token {key}", "Content-Type": content_type},
                    content=audio,
                )
        except httpx.TransportError:
            if attempt == 2:
                raise
            continue
        if response.status_code in RETRY_STATUSES and attempt == 1:
            log.info("Deepgram answered %s; retrying once", response.status_code)
            continue
        if response.status_code >= 400:
            raise AiUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")
        return response
    raise AiUnavailable("Deepgram retry exhausted")


async def transcribe(
    audio: bytes, content_type: str, *, feature: str, user_id: int | None
) -> tuple[str, float]:
    """Returns the transcript and the audio length in seconds."""
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise AiUnavailable("No Deepgram key configured")
    started = time.perf_counter()
    try:
        response = await _post(audio, content_type, settings.deepgram_api_key)
        data = response.json()
        text = data["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        seconds = float(data.get("metadata", {}).get("duration", 0.0))
    except (httpx.HTTPError, KeyError, IndexError, ValueError, AiUnavailable) as error:
        message = f"{type(error).__name__}: {str(error)[:200]}"
        log.warning("Deepgram %s failed: %s", feature, message)
        await record(
            provider="deepgram",
            feature=feature,
            model=MODEL,
            user_id=user_id,
            ok=False,
            error=message[:300],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        raise AiUnavailable(message) from error
    await record(
        provider="deepgram",
        feature=feature,
        model=MODEL,
        user_id=user_id,
        audio_seconds=seconds,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return text, seconds
