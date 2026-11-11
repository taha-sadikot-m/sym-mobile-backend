"""Live debate rules shared by the routes.

Schedules, serialising for the app, safety checks, LiveKit tokens, and saving results to the website.
"""

import secrets
import string
import time
import uuid
from datetime import datetime
from typing import Any

import jwt
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.settings import Features, LiveFormats, LiveRules
from app.core.config import get_settings
from app.models import AppSetting, Profile
from app.models.live import Block, Challenge, LiveEvent
from app.modules.live.clock import Clock, Event, Slot, compute
from app.web import debates as web_debates
from app.web.users import users

CODE_ALPHABET = "".join(c for c in string.ascii_uppercase if c not in "IO")  # no I/O to avoid 1/0 mix-ups


async def setting[T](session: AsyncSession, key: str, model: type[T]) -> T:
    row = await session.get(AppSetting, key)
    return model.model_validate(row.value if row else {})  # type: ignore[attr-defined, no-any-return]


async def live_enabled(session: AsyncSession) -> bool:
    return (await setting(session, "features", Features)).debate_live


async def format_schedule(session: AsyncSession, format_id: str) -> dict[str, Any] | None:
    formats = await setting(session, "live_formats", LiveFormats)
    rules = await setting(session, "live_rules", LiveRules)
    chosen = next((item for item in formats.formats if item.id == format_id and item.enabled), None)
    if chosen is None:
        return None
    return {
        "format": chosen.id,
        "name": chosen.name,
        "slots": [
            {"index": index, "phase": turn.phase, "side": turn.side, "duration_ms": turn.seconds * 1000}
            for index, turn in enumerate(chosen.turns)
        ],
        "grace_ms": rules.grace_seconds * 1000,
        "timeout_ms": rules.timeout_seconds * 1000,
        "crossex_answer_ms": rules.crossex_answer_seconds * 1000,
    }


def slots_of(schedule: dict[str, Any]) -> list[Slot]:
    return [
        Slot(item["index"], item["phase"], item["side"], item["duration_ms"]) for item in schedule["slots"]
    ]


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


async def room_events(session: AsyncSession, challenge_id: uuid.UUID) -> list[LiveEvent]:
    return list(
        await session.scalars(
            select(LiveEvent).where(LiveEvent.challenge_id == challenge_id).order_by(LiveEvent.seq)
        )
    )


def clock_events(events: list[LiveEvent]) -> list[Event]:
    return [
        Event(kind=event.kind, slot=event.slot, at_ms=ms(event.created_at))  # type: ignore[arg-type]
        for event in events
        if event.kind in ("handover", "timeout", "leave")
    ]


def clock_now(challenge: Challenge, events: list[LiveEvent], now_ms: int | None = None) -> Clock:
    schedule = challenge.schedule or {"slots": [], "grace_ms": 5000, "timeout_ms": 30000}
    return compute(
        slots=slots_of(schedule),
        started_at_ms=ms(challenge.started_at) if challenge.started_at else None,
        events=clock_events(events),
        now_ms=now_ms if now_ms is not None else int(time.time() * 1000),
        grace_ms=schedule["grace_ms"],
        timeout_ms=schedule["timeout_ms"],
    )


async def new_code(session: AsyncSession) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not await session.scalar(select(Challenge.id).where(Challenge.code == code)):
            return code


async def blocked_between(session: AsyncSession, a: int, b: int) -> bool:
    return (
        await session.scalar(
            select(Block.blocker_id).where(
                or_(
                    (Block.blocker_id == a) & (Block.blocked_id == b),
                    (Block.blocker_id == b) & (Block.blocked_id == a),
                )
            )
        )
        is not None
    )


async def people(session: AsyncSession, ids: set[int]) -> dict[int, dict[str, str]]:
    """App-shaped LiveUser for each id: {id, name, username, school}."""
    if not ids:
        return {}
    rows = await session.execute(
        select(users.c.id, users.c.full_name, Profile.username, Profile.institution)
        .outerjoin(Profile, Profile.user_id == users.c.id)
        .where(users.c.id.in_(ids))
    )
    return {
        # A student's school is never shown to other students.
        user_id: {"id": str(user_id), "name": name, "username": username or "", "school": ""}
        for user_id, name, username, _school in rows
    }


async def comfort_languages(session: AsyncSession, ids: set[int]) -> dict[str, str]:
    """Each student's comfort language (user id as text, as the judge uses), for their private report."""
    rows = await session.execute(
        select(Profile.user_id, Profile.comfort_language).where(Profile.user_id.in_(ids))
    )
    return {str(user_id): language for user_id, language in rows}


def serialize(challenge: Challenge, who: dict[int, dict[str, str]]) -> dict[str, Any]:
    """The app's Challenge type."""
    unknown = {"id": "", "name": "Waiting for someone to accept", "username": "", "school": ""}
    return {
        "id": str(challenge.id),
        "code": challenge.code,
        "topic": challenge.topic,
        "format": challenge.format,
        "challenger": who.get(challenge.challenger_id, unknown),
        "opponent": who.get(challenge.opponent_id, unknown) if challenge.opponent_id else unknown,
        "sidePreference": challenge.side_preference,
        "challengerSide": challenge.challenger_side,
        "scheduledAt": challenge.scheduled_at.isoformat(),
        "createdAt": challenge.created_at.isoformat(),
        "updatedAt": challenge.updated_at.isoformat(),
        "status": challenge.status,
        "counteredBy": str(challenge.countered_by) if challenge.countered_by else None,
        "counterUsed": challenge.counter_used,
        "reminder": challenge.reminder,
        "resultId": str(challenge.id) if challenge.status == "completed" else None,
        "startedAt": challenge.started_at.isoformat() if challenge.started_at else None,
        "challengerReady": challenge.challenger_ready_at is not None,
        "opponentReady": challenge.opponent_ready_at is not None,
    }


def livekit_token(*, room: str, identity: str, name: str) -> dict[str, str] | None:
    settings = get_settings()
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return None
    now = int(time.time())
    claims = {
        "iss": settings.livekit_api_key,
        "sub": identity,
        "name": name,
        "nbf": now - 10,
        "exp": now + 2 * 60 * 60,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": False,
        },
    }
    return {
        "url": settings.livekit_url,
        "token": jwt.encode(claims, settings.livekit_api_secret, algorithm="HS256"),
        "room": room,
    }


def _when(value: object) -> datetime | None:
    """The app sends ISO timestamps; the website's table needs real datetimes."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def web_scores(categories: list[dict[str, Any]]) -> tuple[float, float, float]:
    score = {item["id"]: float(item["score"]) for item in categories}
    logical = round((score.get("argument", 0) + score.get("evidence", 0) + score.get("structure", 0)) / 3, 2)
    return logical, score.get("delivery", 0.0), score.get("rebuttal", 0.0)


async def save_to_web(
    session: AsyncSession, challenge: Challenge, turns: list[dict[str, Any]], judge: dict[str, Any]
) -> uuid.UUID:
    """Record the finished live debate as a 1v1 Battle (PVP) in the website's tables and update both stats."""
    debate_id = uuid.uuid4()
    assert challenge.opponent_id is not None and challenge.started_at is not None
    ordered = sorted(turns, key=lambda turn: (turn.get("slotIndex", 0), turn.get("createdAt", "")))
    phase_round: dict[int, int] = {}
    for turn in ordered:
        phase_round.setdefault(turn["slotIndex"], len(phase_round) // 2 + 1)
    await web_debates.record_debate(
        session,
        debate_id=debate_id,
        mode="PVP",
        creator_id=challenge.challenger_id,
        opponent_id=challenge.opponent_id,
        topic=challenge.topic.get("title", "Live debate"),
        creator_for=challenge.challenger_side == "for",
        difficulty="MEDIUM",
        started_at=challenge.started_at,
        ended_at=challenge.ended_at or challenge.started_at,
        messages=[
            {
                "id": None,
                "sender_id": int(turn["speakerId"]),
                "text": turn["text"],
                "round": phase_round[turn["slotIndex"]],
                "phase": turn["phase"],
                "input": turn["input"],
                "duration_ms": turn["usedMs"],
                "created_at": _when(turn.get("createdAt")),
            }
            for turn in ordered
            if turn.get("text", "").strip()
        ],
        metadata={"challenge_id": str(challenge.id), "format": challenge.format},
    )
    by_user = {int(entry["userId"]): entry for entry in judge["scores"]}
    creator = web_scores(by_user[challenge.challenger_id]["categories"])
    opponent = web_scores(by_user[challenge.opponent_id]["categories"])
    winner = int(judge["winnerId"])
    await web_debates.record_result(
        session,
        debate_id=debate_id,
        winner_id=winner,
        creator=creator,
        opponent=opponent,
        judge_notes=" ".join(judge.get("reasons", [])) or "Live debate from the SYM app.",
        metadata={"challenge_id": str(challenge.id), "judge": judge},
    )
    for user_id, scores in ((challenge.challenger_id, creator), (challenge.opponent_id, opponent)):
        await web_debates.update_stats(session, user_id=user_id, mode="PVP", winner_id=winner, scores=scores)
    return debate_id


async def pending_count(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count()).where(Challenge.challenger_id == user_id, Challenge.status == "pending")
        )
        or 0
    )
