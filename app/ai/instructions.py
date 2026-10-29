"""Coaching guidance the SYM team edits in the admin console (AI page), added to every Gemini request.

The built-in prompts in code stay the base; this adds the team's own guidance per feature (tone, what to
stress, local examples) and an off switch for AI coaching. Read through a short cache so it costs nothing
per request; the admin console clears the cache when it saves.
"""

import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.db import SessionLocal
from app.models import AppSetting

KEY = "ai_instructions"
CACHE_SECONDS = 30
MAX_CHARS = 1500

FEATURES: dict[str, str] = {
    "mun.opponents": "MUN — choosing the opposing delegations",
    "mun.speech": "MUN — reading the opening speech",
    "mun.pois": "MUN — Points of Information questions",
    "mun.feedback": "MUN — the feedback report",
    "debate.reply": "Debate vs AI — the AI opponent’s turns",
    "debate.analysis": "Debate vs AI — the analysis report",
    "live.judge": "Live 1:1 — the judge and personal reports",
}


class AiInstructions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(True, description="Off: every AI feature uses the app's built-in coach instead.")
    house_style: str = Field("", max_length=MAX_CHARS, description="Guidance for every AI feature.")
    features: dict[str, str] = Field(default_factory=dict, description="Extra guidance per feature.")

    @field_validator("house_style")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("features")
    @classmethod
    def _known(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for feature, text in value.items():
            if feature not in FEATURES:
                raise ValueError(f"Unknown AI feature “{feature}”.")
            if len(text) > MAX_CHARS:
                raise ValueError(f"Guidance for {FEATURES[feature]} is longer than {MAX_CHARS} characters.")
            if text.strip():
                cleaned[feature] = text.strip()
        return cleaned

    def guidance_for(self, feature: str) -> str:
        parts = [self.house_style, self.features.get(feature, "")]
        text = "\n".join(part for part in parts if part)
        if not text:
            return ""
        intro = (
            "Additional guidance from the SYM coaching team. Follow it unless it conflicts with "
            "the rules above, student safety, or the required answer format:"
        )
        return f"\n\n{intro}\n{text}"


_cache: tuple[float, AiInstructions] | None = None


def clear_cache() -> None:
    global _cache
    _cache = None


async def current() -> AiInstructions:
    global _cache
    if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, KEY)
        value = AiInstructions.model_validate(row.value if row else {})
    except Exception:  # never let a settings problem stop coaching
        value = _cache[1] if _cache else AiInstructions()
    _cache = (time.monotonic(), value)
    return value
