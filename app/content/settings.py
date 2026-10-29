"""Rules and settings the app reads from /v1/config, editable in the admin console.

Each group is validated with its own model, so an admin can't save a value the app can't use. Defaults
match the app's built-in behaviour, so an empty table changes nothing.
"""

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class _Group(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpeechSettings(_Group):
    """Opening speech in a MUN session."""

    target_seconds: int = Field(90, ge=30, le=300, description="The Chair's stated speaking time.")
    warn_seconds_left: int = Field(15, ge=5, le=60, description="Amber warning before the target.")
    hard_stop_seconds: int = Field(120, ge=30, le=420, description="Recording stops here.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.hard_stop_seconds < self.target_seconds:
            raise ValueError("The hard stop can’t be before the target time.")
        if self.warn_seconds_left >= self.target_seconds:
            raise ValueError("The warning must come before the target time.")
        return self


class PoiSettings(_Group):
    """Points of Information on the committee floor."""

    count: int = Field(3, ge=1, le=6, description="Questions per session.")
    answer_seconds: int = Field(45, ge=15, le=180)
    follow_up_seconds: int = Field(15, ge=0, le=60, description="0 turns follow-ups off.")
    conclude_warning_seconds: int = Field(10, ge=0, le=30, description="Chair says “Please conclude”.")
    floor_cue_seconds: int = Field(3, ge=0, le=10, description="Pause before the mic opens.")
    allow_repeat: bool = Field(True, description="One “Ask to repeat” per session.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.conclude_warning_seconds >= self.answer_seconds:
            raise ValueError("The conclude warning must come before the answer time runs out.")
        return self


Phase = Literal["opening", "crossex", "rebuttal", "rapid", "closing"]
Side = Literal["for", "against"]


class Turn(_Group):
    phase: Phase
    side: Side
    seconds: int = Field(ge=15, le=600)


class LiveFormat(_Group):
    id: Literal["quick", "standard", "rapid"]
    name: str = Field(min_length=1, max_length=40)
    summary: str = Field(max_length=160)
    best_for: str = Field(max_length=80)
    enabled: bool = True
    turns: list[Turn] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def _fair(self) -> Self:
        time = {"for": 0, "against": 0}
        for turn in self.turns:
            time[turn.side] += turn.seconds
        if time["for"] != time["against"]:
            raise ValueError(f"{self.name}: both sides must get the same total speaking time.")
        return self


def _turns(*spec: tuple[Phase, Side, int]) -> list[Turn]:
    return [Turn(phase=phase, side=side, seconds=seconds) for phase, side, seconds in spec]


class LiveFormats(_Group):
    formats: list[LiveFormat] = Field(
        default_factory=lambda: [
            LiveFormat(
                id="quick",
                name="Quick Clash",
                summary="Opening, rebuttal and closing — 2:00 each.",
                best_for="A great first live debate",
                turns=_turns(
                    ("opening", "for", 120),
                    ("opening", "against", 120),
                    ("rebuttal", "for", 120),
                    ("rebuttal", "against", 120),
                    ("closing", "against", 120),
                    ("closing", "for", 120),
                ),
            ),
            LiveFormat(
                id="standard",
                name="Standard",
                summary="Adds cross-examination: question your opponent directly.",
                best_for="Closest to school competitions",
                turns=_turns(
                    ("opening", "for", 180),
                    ("opening", "against", 180),
                    ("crossex", "against", 120),
                    ("crossex", "for", 120),
                    ("rebuttal", "for", 180),
                    ("rebuttal", "against", 180),
                    ("closing", "against", 120),
                    ("closing", "for", 120),
                ),
            ),
            LiveFormat(
                id="rapid",
                name="Rapid Fire",
                summary="Eight 60-second turns, then short closings.",
                best_for="Fast, high-clash practice",
                turns=[
                    *_turns(*[("rapid", "for" if index % 2 == 0 else "against", 60) for index in range(8)]),
                    *_turns(("closing", "against", 60), ("closing", "for", 60)),
                ],
            ),
        ],
        min_length=1,
    )

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [item.id for item in self.formats]
        if len(ids) != len(set(ids)):
            raise ValueError("Each format can only appear once.")
        if not any(item.enabled for item in self.formats):
            raise ValueError("At least one format must stay enabled.")
        return self


class LiveRules(_Group):
    grace_seconds: int = Field(5, ge=0, le=15, description="“Wrap up” time after a turn hits 0:00.")
    amber_seconds: int = Field(30, ge=5, le=120)
    red_seconds: int = Field(10, ge=3, le=60)
    crossex_answer_seconds: int = Field(30, ge=10, le=90)
    timeout_seconds: int = Field(
        30, ge=0, le=120, description="One prep time-out per debater. 0 turns it off."
    )
    lobby_open_minutes: int = Field(10, ge=1, le=60)
    no_show_grace_minutes: int = Field(5, ge=1, le=30)
    disconnect_pause_seconds: int = Field(60, ge=0, le=300)
    challenge_expiry_hours: int = Field(48, ge=1, le=336)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.red_seconds >= self.amber_seconds:
            raise ValueError("Red must come after amber (fewer seconds left).")
        return self


class Features(_Group):
    mun_single: bool = True
    mun_multi: bool = True
    debate_ai: bool = True
    debate_live: bool = True


class AppVersions(_Group):
    min_supported: str = Field("1.0.0", description="Older apps must update before continuing.")
    latest: str = Field("1.2.0", description="Newer than the installed app shows an optional update.")
    update_message: str = Field("A new version of Speak Your Mind is available.", max_length=200)

    @model_validator(mode="after")
    def _versions(self) -> Self:
        for value in (self.min_supported, self.latest):
            if not VERSION.match(value):
                raise ValueError(f"“{value}” isn’t a version like 1.2.0.")
        if version_tuple(self.min_supported) > version_tuple(self.latest):
            raise ValueError("The minimum supported version can’t be newer than the latest version.")
        return self


class AiCosts(_Group):
    """Used only to estimate spend on the dashboard; check your providers' current prices."""

    gemini_input_per_million: float = Field(0.30, ge=0, le=100, description="USD per 1M input tokens")
    gemini_output_per_million: float = Field(2.50, ge=0, le=100, description="USD per 1M output tokens")
    deepgram_per_minute: float = Field(0.0077, ge=0, le=1, description="USD per minute of audio")
    daily_budget_usd: float = Field(10.0, ge=0, le=10_000, description="Dashboard warns above this")


SETTINGS: dict[str, type[_Group]] = {
    "speech": SpeechSettings,
    "pois": PoiSettings,
    "live_formats": LiveFormats,
    "live_rules": LiveRules,
    "features": Features,
    "app_versions": AppVersions,
    "ai_costs": AiCosts,
}

LABELS: dict[str, str] = {
    "speech": "Opening speech",
    "pois": "Points of Information",
    "live_formats": "Live debate formats",
    "live_rules": "Live debate rules",
    "features": "Feature switches",
    "app_versions": "App versions",
    "ai_costs": "AI cost estimates",
}
