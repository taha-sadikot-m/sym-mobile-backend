import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.core import signing
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.main import app
from app.modules.debates import ai
from app.web.debates import debate_messages, debate_results, debate_sessions, user_debate_stats
from tests.test_student_auth import auth, signed_up_and_verified

TOPIC = {
    "title": "Homework should be abolished in schools",
    "context": "Learning versus wellbeing.",
    "for_points": ["stress"],
    "against_points": ["habits"],
}


@pytest.fixture
async def student() -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        tokens = await signed_up_and_verified(client)
        yield client, auth(tokens)


def fake(feature_answers: dict[str, dict[str, Any]], seen: dict[str, Any] | None = None) -> Any:
    async def generate(*, feature: str, shape: type[BaseModel], prompt: str = "", **_: Any) -> BaseModel:
        if seen is not None:
            seen[feature] = prompt
        if feature not in feature_answers:
            raise ai.AiUnavailable("no fake")
        return shape.model_validate(feature_answers[feature])

    return generate


async def test_reply_argues_the_other_side(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    seen: dict[str, Any] = {}
    speech = "Homework builds discipline and habits that last. " * 4
    monkeypatch.setattr(ai, "generate", fake({"debate.reply": {"text": speech}}, seen))
    response = await client.post(
        "/v1/debates/ai/reply",
        headers=headers,
        json={
            "topic": TOPIC,
            "student_side": "for",
            "difficulty": "hard",
            "phase": "rebuttal",
            "turns": [{"speaker": "user", "phase": "opening", "text": "Homework causes stress."}],
        },
    )
    assert response.status_code == 200
    assert response.json()["text"].startswith("Homework builds discipline")
    assert "You argue AGAINST" in seen["debate.reply"]
    assert "<student>Homework causes stress.</student>" in seen["debate.reply"]
    assert "habits" in seen["debate.reply"]  # the against-side points were offered


async def test_reply_falls_back_when_ai_is_off(student: tuple[httpx.AsyncClient, dict[str, str]]) -> None:
    client, headers = student
    response = await client.post(
        "/v1/debates/ai/reply",
        headers=headers,
        json={
            "topic": TOPIC,
            "student_side": "against",
            "difficulty": "easy",
            "phase": "opening",
            "turns": [],
        },
    )
    assert response.status_code == 503 and response.json()["detail"]["code"] == "ai_unavailable"


async def test_analysis_scores_and_verdict(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student

    def category(category_id: str, score: int) -> dict[str, Any]:
        return {"id": category_id, "score": score, "strengths": ["a"], "improvements": ["b"], "why": "c"}

    answer = {
        "categories": [
            category(name, score)
            for name, score in (
                ("delivery", 70),
                ("argument", 80),
                ("rebuttal", 60),
                ("evidence", 50),
                ("structure", 90),
            )
        ],
        "opponent_score": 66,
        "summary": "Close debate.",
        "key_moments": [
            {"turn_number": 1, "kind": "best", "quote": "stress", "note": "Clear"},
            {"turn_number": 9, "kind": "weakest", "quote": "x", "note": "Invented turn"},
        ],
        "missed_arguments": ["Sleep research"],
        "next_steps": ["Use one statistic"],
    }
    monkeypatch.setattr(ai, "generate", fake({"debate.analysis": answer}))
    response = await client.post(
        "/v1/debates/ai/analysis",
        headers=headers,
        json={
            "topic": TOPIC,
            "student_side": "for",
            "difficulty": "medium",
            "turns": [
                {"speaker": "user", "phase": "opening", "text": "Homework causes stress."},
                {"speaker": "ai", "phase": "opening", "text": "It builds habits."},
            ],
        },
    )
    body = response.json()
    assert [item["id"] for item in body["categories"]] == [
        "argument",
        "rebuttal",
        "evidence",
        "structure",
        "delivery",
    ]
    assert body["overall_score"] == 70 and body["opponent_score"] == 66 and body["verdict"] == "close"
    assert body["seal"]  # sent back when saving, so the website counts the verified result
    assert [moment["turn_number"] for moment in body["key_moments"]] == [1]


def seal_for(user_id: int, score: int, verdict: str) -> str:
    """What /ai/analysis signs for these scores (the server-verified AI analysis)."""
    return signing.sign(
        {
            "uid": user_id,
            "scores": dict.fromkeys(("argument", "rebuttal", "evidence", "structure", "delivery"), score),
            "overall": score,
            "opponent": 60,
            "verdict": verdict,
        },
        secret=get_settings().secret_key,
        purpose="debate-analysis",
        ttl_seconds=600,
    )


def save_body(
    score: int, verdict: str, *, debate_id: uuid.UUID, user_id: int | None = None
) -> dict[str, Any]:
    start = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    turns = [
        {
            "id": str(uuid.uuid4()),
            "speaker": "user",
            "round": 1,
            "phase": "opening",
            "text": "Homework causes stress.",
            "input": "voice",
            "duration_ms": 45000,
            "created_at": start.isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "speaker": "ai",
            "round": 1,
            "phase": "opening",
            "text": "It builds habits.",
            "input": "text",
            "created_at": (start + timedelta(minutes=1)).isoformat(),
        },
    ]
    return {
        "topic": TOPIC["title"],
        "student_side": "against",
        "difficulty": "hard",
        "started_at": start.isoformat(),
        "ended_at": (start + timedelta(minutes=8)).isoformat(),
        "turns": turns,
        "scores": {
            "argument": score,
            "evidence": score,
            "structure": score,
            "delivery": score,
            "rebuttal": score,
        },
        "overall_score": score,
        "opponent_score": 60,
        "verdict": verdict,
        "summary": "Good work.",
        "next_steps": ["Practise closings"],
        "analysis": {"overallScore": score},
        "ai_analysis": True,
        "seal": seal_for(user_id, score, verdict) if user_id is not None else "",
    }


async def test_saved_debates_appear_in_the_website_tables(
    student: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = student
    me = (await client.get("/v1/me", headers=headers)).json()
    first, second = uuid.uuid4(), uuid.uuid4()

    saved = await client.put(
        f"/v1/debates/ai/sessions/{first}",
        headers=headers,
        json=save_body(80, "won", debate_id=first, user_id=me["id"]),
    )
    assert saved.json() == {"saved": True, "already_saved": False}
    again = await client.put(
        f"/v1/debates/ai/sessions/{first}",
        headers=headers,
        json=save_body(80, "won", debate_id=first, user_id=me["id"]),
    )
    assert again.json() == {"saved": True, "already_saved": True}
    await client.put(
        f"/v1/debates/ai/sessions/{second}",
        headers=headers,
        json=save_body(60, "lost", debate_id=second, user_id=me["id"]),
    )

    async with SessionLocal() as session:
        row = (
            (await session.execute(select(debate_sessions).where(debate_sessions.c.id == first)))
            .one()
            ._mapping
        )
        assert (row["mode"], row["status"], row["is_ai_opponent"]) == ("PRACTICE", "COMPLETED", True)
        assert (row["creator_position"], row["opponent_position"], row["opening_speaker"]) == (
            "CON",
            "PRO",
            "OPPONENT",
        )
        assert (
            row["difficulty"] == "HARD"
            and row["metadata"]["source"] == "mobile"
            and row["creator_id"] == me["id"]
        )

        messages = (
            await session.execute(
                select(debate_messages)
                .where(debate_messages.c.debate_id == first)
                .order_by(debate_messages.c.sequence_number)
            )
        ).all()
        assert [(m.is_ai_message, m.sender_id, m.audio_duration) for m in messages] == [
            (False, me["id"], 45),
            (True, None, None),
        ]

        won = (await session.execute(select(debate_results).where(debate_results.c.debate_id == first))).one()
        assert won.winner_id == me["id"] and won.judged_by == "AI"
        assert (
            won.creator_total_score
            == won.creator_logical_score + won.creator_emotional_score + won.creator_rebuttal_score
        )
        lost = (
            await session.execute(select(debate_results).where(debate_results.c.debate_id == second))
        ).one()
        assert lost.winner_id is None  # the web counts a non-win practice debate as a draw

        stats = (
            await session.execute(select(user_debate_stats).where(user_debate_stats.c.user_id == me["id"]))
        ).one()
        assert (stats.total_debates, stats.practice_debates, stats.total_wins, stats.total_draws) == (
            2,
            2,
            1,
            1,
        )
        assert (stats.current_win_streak, stats.longest_win_streak) == (0, 1)
        assert stats.avg_rebuttal_score == Decimal("70.00") and stats.avg_total_score == Decimal("210.00")


async def test_someone_else_cannot_overwrite_a_debate(
    student: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = student
    debate = uuid.uuid4()
    await client.put(
        f"/v1/debates/ai/sessions/{debate}", headers=headers, json=save_body(70, "won", debate_id=debate)
    )
    other = auth(await signed_up_and_verified(client, "kabir@school.in"))
    response = await client.put(
        f"/v1/debates/ai/sessions/{debate}", headers=other, json=save_body(90, "won", debate_id=debate)
    )
    assert response.status_code == 409


async def test_skill_ladder_follows_the_website_stats(
    student: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = student
    me = (await client.get("/v1/me", headers=headers)).json()
    empty = (await client.get("/v1/me/ladder", headers=headers)).json()
    assert (empty["ladder"]["level"], empty["ladder"]["lifetime_score"], empty["total_debates"]) == (
        "Beginner",
        0,
        0,
    )
    debate_id = uuid.uuid4()
    await client.put(
        f"/v1/debates/ai/sessions/{debate_id}",
        headers=headers,
        json=save_body(80, "won", debate_id=debate_id, user_id=me["id"]),
    )
    ladder = (await client.get("/v1/me/ladder", headers=headers)).json()
    assert (ladder["total_debates"], ladder["wins"], ladder["win_streak"]) == (1, 1, 1)
    # One debate: 35 for the debate + score // 5 + a 2-point streak bonus.
    assert ladder["ladder"]["lifetime_score"] == 35 + ladder["ladder"]["score"] // 5 + 2


async def test_unverified_scores_cannot_make_a_win(student: tuple[httpx.AsyncClient, dict[str, str]]) -> None:
    client, headers = student
    me = (await client.get("/v1/me", headers=headers)).json()
    forged = save_body(98, "won", debate_id=uuid.uuid4())
    await client.put(f"/v1/debates/ai/sessions/{uuid.uuid4()}", headers=headers, json=forged)
    tampered = save_body(98, "won", debate_id=uuid.uuid4()) | {"seal": seal_for(me["id"], 60, "lost")}
    await client.put(f"/v1/debates/ai/sessions/{uuid.uuid4()}", headers=headers, json=tampered)
    async with SessionLocal() as session:
        stats = (
            await session.execute(select(user_debate_stats).where(user_debate_stats.c.user_id == me["id"]))
        ).one()
    assert (stats.total_debates, stats.total_wins, stats.total_draws) == (2, 0, 2)
    assert stats.avg_rebuttal_score == Decimal("70.00")  # capped without a verified analysis
