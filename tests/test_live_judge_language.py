"""Live AI judge: the shared verdict in English; each private report in that debater's comfort language."""

from typing import Any, Literal

import pytest
from pydantic import BaseModel

from app.modules.live import judge


def turn(speaker: str, slot: int, side: Literal["for", "against"], text: str) -> judge.Turn:
    return judge.Turn(
        id=f"t{slot}",
        slotIndex=slot,
        speakerId=speaker,
        side=side,
        phase="opening",
        role="speech",
        text=text,
        input="voice",
        usedMs=90_000,
        allowedMs=120_000,
        cutOff=False,
        createdAt="2026-11-12T10:00:00Z",
    )


TURNS = [turn("1", 0, "for", "Homework cuts sleep."), turn("2", 1, "against", "Homework builds habits.")]


def debater(letter: str, *, why_own: str | None, meaning: str | None) -> dict[str, Any]:
    return {
        "debater": letter,
        "categories": [
            {"id": name, "score": 70, "why": f"You did {name} well.", "why_own": why_own}
            for name in ("argument", "rebuttal", "evidence", "structure", "delivery")
        ],
        "strengths": [{"turn_number": 1, "quote": "sleep", "note": "Clear point."}],
        "mistakes": [
            {
                "turn_number": 1,
                "quote": "I am having a doubt",
                "note": "Sounds unsure.",
                "better": "I have a question",
                "better_meaning": meaning,
            }
        ],
        "turn_ratings": [{"turn_number": 1, "rating": "strong", "verdict": "Good."}],
        "unanswered": [],
        "next_steps": ["Practise one rebuttal."],
    }


def fake(answer: dict[str, Any], seen: dict[str, Any]) -> Any:
    async def generate(*, shape: type[BaseModel], **kwargs: Any) -> BaseModel:
        seen.update(kwargs)
        return shape.model_validate(answer)

    return generate


async def test_each_private_report_follows_its_debaters_language(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    answer = {
        "debaters": [
            debater("A", why_own="நீங்கள் நன்றாக வாதிட்டீர்கள்.", meaning="எனக்கு ஒரு கேள்வி உள்ளது"),
            debater("B", why_own="ignored for English", meaning="ignored for English"),
        ],
        "reasons": ["Debater A answered more points."],
        "clashes": [{"title": "Sleep", "winner": "A", "why": "Debater A gave a reason."}],
    }
    monkeypatch.setattr(judge, "generate", fake(answer, seen))
    result = await judge.judge(
        motion="Homework should be abolished",
        turns=TURNS,
        debaters={"1": "A", "2": "B"},
        user_id=1,
        names={"1": "Riya Mehta", "2": "Kabir Shah"},
        languages={"1": "ta"},  # Kabir has no language on file: English
    )
    assert seen["language"] == "ta+en"
    assert (
        "Debater A: Language: the student practises speaking English and is most comfortable in Tamil"
        in (seen["system"])
    )
    assert "Debater B: Language: the student practises speaking English. Write" in seen["system"]

    # The shared verdict stays English, with first names.
    verdict = result["judge"]
    assert verdict["reasons"] == ["Riya answered more points."]
    assert verdict["scores"][0]["categories"][0]["why"] == "You did argument well."

    riya, kabir = result["reports"]["1"], result["reports"]["2"]
    assert riya["language"] == "ta" and kabir["language"] == "en"
    assert riya["scorecard"][0]["why"] == "நீங்கள் நன்றாக வாதிட்டீர்கள்."
    assert riya["scorecard"][-1]["id"] == "time"  # computed here, unchanged
    assert riya["mistakes"][0]["better"] == "I have a question"
    assert riya["mistakes"][0]["betterMeaning"] == "எனக்கு ஒரு கேள்வி உள்ளது"
    assert kabir["scorecard"][0]["why"] == "You did argument well."
    assert "betterMeaning" not in kabir["mistakes"][0]


async def test_english_debaters_get_the_english_rule_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    answer = {
        "debaters": [debater("A", why_own=None, meaning=None), debater("B", why_own=None, meaning=None)],
        "reasons": ["Close."],
        "clashes": [],
    }
    monkeypatch.setattr(judge, "generate", fake(answer, seen))
    result = await judge.judge(
        motion="Homework should be abolished", turns=TURNS, debaters={"1": "A", "2": "B"}, user_id=1
    )
    assert seen["language"] == "en" and "Debater A:" not in seen["system"]
    # The same report shape as before the comfort language, plus its language.
    assert result["reports"]["1"]["language"] == "en"
    assert result["reports"]["1"]["mistakes"][0] == {
        "turnId": "t0",
        "where": "Opening · 90s",
        "quote": "I am having a doubt",
        "note": "Sounds unsure.",
        "better": "I have a question",
    }
