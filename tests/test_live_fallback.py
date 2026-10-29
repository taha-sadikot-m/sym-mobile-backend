"""The server's built-in live judge must decide exactly as the app's src/features/live/judge.ts.

tests/fixtures_live_judge.json was produced by running the TypeScript judge on these turns; regenerate it
(see the fixture's turns) whenever judge.ts changes.
"""

import json
from pathlib import Path

import pytest

from app.modules.live import fallback
from app.modules.live.judge import Turn

CASES = json.loads((Path(__file__).parent / "fixtures_live_judge.json").read_text(encoding="utf-8"))
USERS = [("11", "Riya"), ("22", "Kabir")]


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_the_app_judge(name: str) -> None:
    case = CASES[name]
    turns = [Turn.model_validate(turn) for turn in case["turns"]]
    assert fallback.judge_debate(turns, USERS) == case["judge"]


def test_leaving_early_is_a_forfeit() -> None:
    judge = {
        "winnerId": "11",
        "margin": 4,
        "scores": [],
        "reasons": ["Riya wins by 4 points."],
        "clashes": [],
    }
    forfeited = fallback.apply_forfeit(judge, leaver_id="11", names=dict(USERS))
    assert forfeited["winnerId"] == "22" and forfeited["forfeit"] is True
    assert forfeited["reasons"][0] == "Riya left the debate early, so Kabir wins."
    assert fallback.apply_forfeit(judge, leaver_id="22", names=dict(USERS)) == judge


def test_ai_verdict_text_uses_first_names() -> None:
    from app.modules.live.judge import with_names

    text = {
        "reasons": ["Debater A won; debater B rebutted well."],
        "clashes": [{"why": "Debater B answered."}],
    }
    named = with_names(text, {"A": "Riya", "B": "Kabir"})
    assert named == {"reasons": ["Riya won; Kabir rebutted well."], "clashes": [{"why": "Kabir answered."}]}
