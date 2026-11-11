"""Gemini, asked for JSON in a fixed shape and checked against a Pydantic model.

Every call is logged to `mobile.ai_calls` (tokens, time, success, the language it answered in) in its own
transaction. Callers get an `AiUnavailable` error when the key is missing, the service fails twice, or the
answer doesn't fit the shape — and fall back to the built-in heuristics, so students are never stuck
waiting on AI.
"""

import json
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.ai import instructions
from app.ai.usage import record
from app.core.config import get_settings

log = logging.getLogger("sym.ai")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SECONDS = 45


class AiUnavailable(Exception):
    """AI couldn't produce a usable answer; the caller should use its fallback."""


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic's JSON schema, reduced to the subset Gemini's responseSchema accepts."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return clean(definitions[node["$ref"].split("/")[-1]])
            if "anyOf" in node:  # Optional[X] -> X, marked nullable
                options = [option for option in node["anyOf"] if option.get("type") != "null"]
                merged = clean(options[0]) if options else {"type": "string"}
                return {**merged, "nullable": True}
            kept = {
                key: clean(value)
                for key, value in node.items()
                if key in {"type", "items", "required", "enum", "description", "minItems", "maxItems"}
            }
            if "properties" in node:  # keys here are field names, not schema keywords
                kept["properties"] = {name: clean(child) for name, child in node["properties"].items()}
            return kept
        if isinstance(node, list):
            return [clean(item) for item in node]
        return node

    return clean(schema)  # type: ignore[no-any-return]


async def generate[T: BaseModel](
    *,
    feature: str,
    system: str,
    prompt: str,
    shape: type[T],
    user_id: int | None = None,
    temperature: float = 0.4,
    thinking_budget: int = 0,
    language: str | None = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> T:
    """`thinking_budget` 0 answers fastest; a few hundred tokens helps careful judgement (e.g. scoring).

    `language` is only logged (the prompt carries the language rule); `timeout_seconds` is per attempt
    (there are two attempts).
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        raise AiUnavailable("No Gemini key configured")
    guide = await instructions.current()
    if not guide.enabled:
        raise AiUnavailable("AI coaching is switched off in the admin console")
    body = {
        "systemInstruction": {"parts": [{"text": system + guide.guidance_for(feature)}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": _schema(shape),
            "thinkingConfig": {"thinkingBudget": thinking_budget},
        },
    }
    url = ENDPOINT.format(model=settings.gemini_model)
    last_error = "unknown"
    for attempt in (1, 2):
        started = time.perf_counter()
        usage: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    url, headers={"x-goog-api-key": settings.gemini_api_key}, json=body
                )
            if response.status_code >= 400:
                raise AiUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")
            data = response.json()
            usage = data.get("usageMetadata", {})
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = shape.model_validate(json.loads(text))
            await record(
                provider="gemini",
                feature=feature,
                model=settings.gemini_model,
                user_id=user_id,
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                latency_ms=int((time.perf_counter() - started) * 1000),
                language=language,
            )
            return result
        except (
            httpx.HTTPError,
            AiUnavailable,
            KeyError,
            IndexError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            last_error = f"{type(error).__name__}: {str(error)[:200]}"
            log.warning("Gemini %s attempt %s failed: %s", feature, attempt, last_error)
            await record(
                provider="gemini",
                feature=feature,
                model=settings.gemini_model,
                user_id=user_id,
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                latency_ms=int((time.perf_counter() - started) * 1000),
                ok=False,
                error=last_error[:300],
                language=language,
            )
    raise AiUnavailable(last_error)
