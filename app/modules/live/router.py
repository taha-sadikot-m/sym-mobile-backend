"""Live 1:1 debates between students, for the app.

Challenges (with invite codes), the lobby, the room, the judge, results, and safety (report, block).
The room is never pushed: both phones fetch /v1/live/{id}/state about once a second and compute the same
clock from the start time and event log (see clock.py), using `server_now` to correct their own clocks.
"""

import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.ai.gemini import AiUnavailable
from app.content.settings import LiveRules
from app.core import ratelimit
from app.models import AppStatus, Profile
from app.models.base import utcnow
from app.models.live import Block, Challenge, ChallengeStatus, LiveEvent, LiveResult, UserReport
from app.modules.auth.deps import CurrentStudent, Session
from app.modules.live import alerts, fallback, service
from app.modules.live import judge as live_judge
from app.modules.live.clock import done_at_ms
from app.web.users import users

router = APIRouter(prefix="/v1", tags=["live debates"])

ACTIVE = (ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED, ChallengeStatus.COUNTERED, ChallengeStatus.LIVE)
START_COUNTDOWN = timedelta(seconds=5)
JUDGE_WAIT_MS = 60_000  # after the last turn ends, wait this long for a late transcript before judging


def problem(code: int, message: str, kind: str) -> HTTPException:
    return HTTPException(code, detail={"message": message, "code": kind})


async def _mine(session: Session, challenge_id: uuid.UUID, user_id: int) -> Challenge:
    challenge = await session.get(Challenge, challenge_id)
    if challenge is None or user_id not in (challenge.challenger_id, challenge.opponent_id):
        raise problem(status.HTTP_404_NOT_FOUND, "Challenge not found.", "not_found")
    return challenge


async def _out(session: Session, *challenges: Challenge) -> list[dict[str, Any]]:
    ids = {c.challenger_id for c in challenges} | {c.opponent_id for c in challenges if c.opponent_id}
    who = await service.people(session, ids)
    return [service.serialize(challenge, who) for challenge in challenges]


def _expire(challenge: Challenge) -> None:
    if (
        challenge.status in (ChallengeStatus.PENDING, ChallengeStatus.COUNTERED)
        and challenge.expires_at < utcnow()
    ):
        challenge.status = ChallengeStatus.EXPIRED


# ---------- Finding opponents ----------


class PersonOut(BaseModel):
    id: str
    name: str
    username: str
    school: str


@router.get("/users/search")
async def search_users(
    student: CurrentStudent, session: Session, q: Annotated[str, Query(min_length=2, max_length=60)]
) -> list[PersonOut]:
    """Other app students by @username or name. Blocked people and paused accounts never appear."""
    await ratelimit.hit(f"search:{student.id}", limit=120, per_seconds=60 * 10)
    needle = q.strip().lstrip("@").lower()
    blocked = (
        select(Block.blocked_id)
        .where(Block.blocker_id == student.id)
        .union(select(Block.blocker_id).where(Block.blocked_id == student.id))
    )
    rows = await session.execute(
        select(users.c.id, users.c.full_name, Profile.username, Profile.institution)
        .join(Profile, Profile.user_id == users.c.id)
        .where(
            users.c.id != student.id,
            users.c.is_active,
            Profile.app_status == AppStatus.ACTIVE,
            users.c.id.not_in(blocked),
            or_(func.lower(Profile.username).startswith(needle), users.c.full_name.ilike(f"%{needle}%")),
        )
        .order_by(func.lower(Profile.username) == needle, users.c.full_name)
        .limit(10)
    )
    return [
        PersonOut(id=str(uid), name=name, username=username or "", school="")
        for uid, name, username, _school in rows
    ]


# ---------- Challenges ----------


class TopicIn(BaseModel):
    id: str = Field(max_length=96)
    title: str = Field(min_length=5, max_length=300)
    category: str = Field("custom", max_length=32)
    context: str = Field("", max_length=600)


class ChallengeIn(BaseModel):
    topic: TopicIn
    format: Literal["quick", "standard", "rapid"]
    side_preference: Literal["for", "against", "toss"]
    scheduled_at: datetime
    reminder: bool = True
    opponent_id: int | None = Field(None, description="Leave empty to create an open invite link")


@router.post("/challenges", status_code=status.HTTP_201_CREATED)
async def create_challenge(
    body: ChallengeIn, student: CurrentStudent, session: Session, background: BackgroundTasks
) -> dict[str, Any]:
    if not await service.live_enabled(session):
        raise problem(status.HTTP_403_FORBIDDEN, "Live debates are switched off right now.", "feature_off")
    await ratelimit.hit(f"challenge:{student.id}", limit=30, per_seconds=24 * 60 * 60)
    if await service.pending_count(session, student.id) >= 10:
        raise problem(
            status.HTTP_409_CONFLICT, "You have 10 challenges waiting. Cancel one first.", "too_many"
        )
    if await service.format_schedule(session, body.format) is None:
        raise problem(status.HTTP_409_CONFLICT, "That format isn’t available right now.", "format_off")
    if body.scheduled_at < utcnow() - timedelta(minutes=5):
        raise problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a time in the future.", "invalid_time")
    if body.opponent_id is not None:
        if body.opponent_id == student.id:
            raise problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "You can’t challenge yourself.", "invalid_opponent"
            )
        opponent = await session.get(Profile, body.opponent_id)
        if opponent is None or opponent.app_status != AppStatus.ACTIVE:
            raise problem(status.HTTP_404_NOT_FOUND, "That student isn’t available.", "not_found")
        if await service.blocked_between(session, student.id, body.opponent_id):
            raise problem(status.HTTP_404_NOT_FOUND, "That student isn’t available.", "not_found")
    rules = await service.setting(session, "live_rules", LiveRules)
    challenge = Challenge(
        code=await service.new_code(session),
        challenger_id=student.id,
        opponent_id=body.opponent_id,
        topic=body.topic.model_dump(),
        format=body.format,
        side_preference=body.side_preference,
        challenger_side=None if body.side_preference == "toss" else body.side_preference,
        scheduled_at=body.scheduled_at,
        reminder=body.reminder,
        expires_at=utcnow() + timedelta(hours=rules.challenge_expiry_hours),
    )
    session.add(challenge)
    await session.commit()
    if challenge.opponent_id is not None:
        alerts.send(
            background,
            "challenge.received",
            [challenge.opponent_id],
            challenge=challenge,
            actor=student.user.full_name,
        )
    return (await _out(session, challenge))[0]


@router.get("/challenges")
async def list_challenges(student: CurrentStudent, session: Session) -> list[dict[str, Any]]:
    rows = list(
        await session.scalars(
            select(Challenge)
            .where(or_(Challenge.challenger_id == student.id, Challenge.opponent_id == student.id))
            .order_by(Challenge.scheduled_at.desc())
            .limit(100)
        )
    )
    for challenge in rows:
        _expire(challenge)
    await session.commit()
    return await _out(session, *rows)


@router.get("/challenges/code/{code}")
async def open_invite(
    code: str, student: CurrentStudent, session: Session, background: BackgroundTasks
) -> dict[str, Any]:
    """Opening an invite link: an open challenge is claimed by the first student who opens it."""
    await ratelimit.hit(f"invite:{student.id}", limit=30, per_seconds=60 * 60)
    challenge = await session.scalar(select(Challenge).where(Challenge.code == code.strip().upper()))
    if challenge is None:
        raise problem(status.HTTP_404_NOT_FOUND, "That invite link isn’t valid.", "not_found")
    _expire(challenge)
    if challenge.opponent_id is None and challenge.challenger_id != student.id:
        if challenge.status != ChallengeStatus.PENDING:
            raise problem(status.HTTP_410_GONE, "This invite has expired.", "expired")
        if await service.blocked_between(session, challenge.challenger_id, student.id):
            raise problem(status.HTTP_404_NOT_FOUND, "That invite link isn’t valid.", "not_found")
        challenge.opponent_id = student.id
        alerts.send(
            background,
            "challenge.claimed",
            [challenge.challenger_id],
            challenge=challenge,
            actor=student.user.full_name,
        )
    elif student.id not in (challenge.challenger_id, challenge.opponent_id):
        raise problem(status.HTTP_409_CONFLICT, "Someone else already took this challenge.", "taken")
    await session.commit()
    return (await _out(session, challenge))[0]


@router.get("/challenges/{challenge_id}")
async def get_challenge(challenge_id: uuid.UUID, student: CurrentStudent, session: Session) -> dict[str, Any]:
    challenge = await _mine(session, challenge_id, student.id)
    _expire(challenge)
    await session.commit()
    return (await _out(session, challenge))[0]


@router.post("/challenges/{challenge_id}/accept")
async def accept(
    challenge_id: uuid.UUID, student: CurrentStudent, session: Session, background: BackgroundTasks
) -> dict[str, Any]:
    challenge = await _mine(session, challenge_id, student.id)
    _expire(challenge)
    waiting_on_me = (challenge.status == ChallengeStatus.PENDING and challenge.opponent_id == student.id) or (
        challenge.status == ChallengeStatus.COUNTERED and challenge.countered_by != student.id
    )
    if not waiting_on_me:
        raise problem(status.HTTP_409_CONFLICT, "This challenge can’t be accepted now.", "invalid_state")
    challenge.status = ChallengeStatus.ACCEPTED
    if challenge.side_preference == "toss" and challenge.challenger_side is None:
        challenge.challenger_side = random.choice(["for", "against"])  # noqa: S311 - a coin toss, not security
    await session.commit()
    alerts.send(
        background,
        "challenge.accepted",
        [challenge.opponent_id if student.id == challenge.challenger_id else challenge.challenger_id],
        challenge=challenge,
        actor=student.user.full_name,
    )
    return (await _out(session, challenge))[0]


@router.post("/challenges/{challenge_id}/decline")
async def decline(
    challenge_id: uuid.UUID, student: CurrentStudent, session: Session, background: BackgroundTasks
) -> dict[str, Any]:
    challenge = await _mine(session, challenge_id, student.id)
    if challenge.opponent_id != student.id or challenge.status not in (
        ChallengeStatus.PENDING,
        ChallengeStatus.COUNTERED,
    ):
        raise problem(status.HTTP_409_CONFLICT, "This challenge can’t be declined now.", "invalid_state")
    challenge.status = ChallengeStatus.DECLINED
    await session.commit()
    alerts.send(
        background,
        "challenge.declined",
        [challenge.challenger_id],
        challenge=challenge,
        actor=student.user.full_name,
    )
    return (await _out(session, challenge))[0]


class CounterIn(BaseModel):
    scheduled_at: datetime


@router.post("/challenges/{challenge_id}/counter")
async def counter(
    challenge_id: uuid.UUID,
    body: CounterIn,
    student: CurrentStudent,
    session: Session,
    background: BackgroundTasks,
) -> dict[str, Any]:
    challenge = await _mine(session, challenge_id, student.id)
    if challenge.counter_used or challenge.status not in (ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED):
        raise problem(status.HTTP_409_CONFLICT, "A new time can only be proposed once.", "invalid_state")
    if body.scheduled_at < utcnow():
        raise problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a time in the future.", "invalid_time")
    challenge.scheduled_at = body.scheduled_at
    challenge.status = ChallengeStatus.COUNTERED
    challenge.countered_by = student.id
    challenge.counter_used = True
    challenge.challenger_ready_at = challenge.opponent_ready_at = None
    challenge.reminded_at = None
    await session.commit()
    alerts.send(
        background,
        "challenge.countered",
        [challenge.opponent_id if student.id == challenge.challenger_id else challenge.challenger_id],
        challenge=challenge,
        actor=student.user.full_name,
    )
    return (await _out(session, challenge))[0]


@router.post("/challenges/{challenge_id}/cancel")
async def cancel(
    challenge_id: uuid.UUID, student: CurrentStudent, session: Session, background: BackgroundTasks
) -> dict[str, Any]:
    challenge = await _mine(session, challenge_id, student.id)
    if challenge.status not in (ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED, ChallengeStatus.COUNTERED):
        raise problem(status.HTTP_409_CONFLICT, "This challenge can’t be cancelled now.", "invalid_state")
    challenge.status = ChallengeStatus.CANCELLED
    await session.commit()
    alerts.send(
        background,
        "challenge.cancelled",
        [challenge.opponent_id if student.id == challenge.challenger_id else challenge.challenger_id],
        challenge=challenge,
        actor=student.user.full_name,
    )
    return (await _out(session, challenge))[0]


@router.post("/challenges/{challenge_id}/ready")
async def ready(
    challenge_id: uuid.UUID, student: CurrentStudent, session: Session, background: BackgroundTasks
) -> dict[str, Any]:
    """Mark yourself ready in the lobby; when both are ready the room starts after a short countdown."""
    challenge = await _mine(session, challenge_id, student.id)
    if challenge.status == ChallengeStatus.LIVE:
        return (await _out(session, challenge))[0]
    if challenge.status != ChallengeStatus.ACCEPTED:
        raise problem(status.HTTP_409_CONFLICT, "The challenge must be accepted first.", "invalid_state")
    now = utcnow()
    if student.id == challenge.challenger_id:
        challenge.challenger_ready_at = now
    else:
        challenge.opponent_ready_at = now
    if challenge.challenger_ready_at and challenge.opponent_ready_at:
        schedule = await service.format_schedule(session, challenge.format)
        if schedule is None:
            raise problem(status.HTTP_409_CONFLICT, "That format isn’t available right now.", "format_off")
        challenge.schedule = schedule
        challenge.started_at = now + START_COUNTDOWN
        challenge.status = ChallengeStatus.LIVE
    await session.commit()
    _forget_room(challenge.id)
    if challenge.status == ChallengeStatus.ACCEPTED:
        alerts.send(
            background,
            "challenge.lobby",
            [challenge.opponent_id if student.id == challenge.challenger_id else challenge.challenger_id],
            challenge=challenge,
            actor=student.user.full_name,
        )
    return (await _out(session, challenge))[0]


# ---------- The room ----------


def _clock_out(challenge: Challenge, events: list[LiveEvent], now_ms: int) -> dict[str, Any]:
    clock = service.clock_now(challenge, events, now_ms)
    return {
        "stage": clock.stage,
        "slotIndex": clock.slot_index,
        "cueEndsAt": clock.cue_ends_at_ms,
        "liveStartedAt": clock.live_started_at_ms,
        "endsAt": clock.ends_at_ms,
        "timeoutSlots": sorted(clock.timeout_slots),
    }


@router.get("/live/{challenge_id}/state")
async def room_state(challenge_id: uuid.UUID, student: CurrentStudent, session: Session) -> dict[str, Any]:
    cached = _rooms.get(challenge_id)
    if cached and time.monotonic() - cached.at < ROOM_CACHE_SECONDS and student.id in cached.members:
        challenge, events, challenge_out, has_result = (
            cached.challenge,
            cached.events,
            cached.out,
            cached.judged,
        )
    else:
        challenge = await _mine(session, challenge_id, student.id)
        events = await service.room_events(session, challenge.id)
        has_result = await session.get(LiveResult, challenge.id) is not None
        challenge_out = (await _out(session, challenge))[0]
        _remember_room(challenge, events, challenge_out, has_result)
    now_ms = int(time.time() * 1000)
    return {
        "challenge": challenge_out,
        "serverNow": now_ms,
        "startedAt": service.ms(challenge.started_at) if challenge.started_at else None,
        "schedule": challenge.schedule,
        "events": [
            {
                "kind": event.kind,
                "slot": event.slot,
                "userId": str(event.user_id),
                "at": service.ms(event.created_at),
            }
            for event in events
            if event.kind != "turn"
        ],
        "turns": [event.payload for event in events if event.kind == "turn"],
        "clock": _clock_out(challenge, events, now_ms),
        "resultReady": has_result,
    }


@dataclass
class _Room:
    at: float
    members: tuple[int, int | None]
    challenge: Challenge
    events: list[LiveEvent]
    out: dict[str, Any]
    judged: bool


# Both phones poll the room every second; they share one database read per half second. Any change to the
# room clears its entry, so the person who acted sees it at once (another worker within half a second).
ROOM_CACHE_SECONDS = 0.5
_rooms: dict[uuid.UUID, _Room] = {}


def _remember_room(challenge: Challenge, events: list[LiveEvent], out: dict[str, Any], judged: bool) -> None:
    now = time.monotonic()
    if len(_rooms) > 2000:
        for key in [key for key, room in _rooms.items() if now - room.at >= ROOM_CACHE_SECONDS]:
            _rooms.pop(key, None)
    _rooms[challenge.id] = _Room(
        now, (challenge.challenger_id, challenge.opponent_id), challenge, events, out, judged
    )


def _forget_room(challenge_id: uuid.UUID) -> None:
    _rooms.pop(challenge_id, None)


class EventIn(BaseModel):
    kind: Literal["handover", "timeout", "leave"]
    slot: int | None = Field(None, ge=0, le=40)


async def _append(
    session: Session, challenge: Challenge, kind: str, user_id: int, slot: int | None, payload: dict[str, Any]
) -> None:
    # Serialise appends per room so sequence numbers never collide.
    await session.execute(select(Challenge.id).where(Challenge.id == challenge.id).with_for_update())
    seq = (
        await session.scalar(select(func.max(LiveEvent.seq)).where(LiveEvent.challenge_id == challenge.id))
    ) or 0
    session.add(
        LiveEvent(
            challenge_id=challenge.id, seq=seq + 1, kind=kind, user_id=user_id, slot=slot, payload=payload
        )
    )


def _side_of(challenge: Challenge, user_id: int) -> str:
    side = challenge.challenger_side or "for"
    return side if user_id == challenge.challenger_id else ("against" if side == "for" else "for")


@router.post("/live/{challenge_id}/events", status_code=status.HTTP_204_NO_CONTENT)
async def room_event(
    challenge_id: uuid.UUID, body: EventIn, student: CurrentStudent, session: Session
) -> None:
    challenge = await _mine(session, challenge_id, student.id)
    if challenge.status != ChallengeStatus.LIVE or challenge.schedule is None:
        raise problem(status.HTTP_409_CONFLICT, "The debate isn’t running.", "not_live")
    events = await service.room_events(session, challenge.id)
    clock = service.clock_now(challenge, events)
    mine = _side_of(challenge, student.id)
    slot = challenge.schedule["slots"][clock.slot_index] if clock.stage in ("cue", "live") else None
    if body.kind == "leave":
        await _append(session, challenge, "leave", student.id, None, {})
    elif slot is None or body.slot != clock.slot_index:
        raise problem(status.HTTP_409_CONFLICT, "That turn has already passed.", "stale_slot")
    elif slot["side"] != mine:
        raise problem(status.HTTP_403_FORBIDDEN, "It isn’t your turn.", "not_your_turn")
    elif body.kind == "handover":
        if clock.stage != "live":
            raise problem(status.HTTP_409_CONFLICT, "Your turn hasn’t started yet.", "not_started")
        await _append(session, challenge, "handover", student.id, clock.slot_index, {})
    else:  # time-out: once per debater, only during the cue before their own turn
        used = any(event.kind == "timeout" and event.user_id == student.id for event in events)
        if used or clock.stage != "cue" or not challenge.schedule.get("timeout_ms"):
            raise problem(status.HTTP_409_CONFLICT, "You can’t take a time-out now.", "timeout_unavailable")
        await _append(session, challenge, "timeout", student.id, clock.slot_index, {})
    await session.commit()
    _forget_room(challenge.id)


class TurnIn(live_judge.Turn):
    id: str = Field(min_length=1, max_length=64)
    phase: str = Field("", max_length=16)
    createdAt: str = Field("", max_length=40)


@router.post("/live/{challenge_id}/turns", status_code=status.HTTP_204_NO_CONTENT)
async def room_turn(challenge_id: uuid.UUID, body: TurnIn, student: CurrentStudent, session: Session) -> None:
    """A finished turn's transcript, sent by the speaker. Re-sending the same turn id replaces it."""
    challenge = await _mine(session, challenge_id, student.id)
    if challenge.schedule is None or challenge.status not in (
        ChallengeStatus.LIVE,
        ChallengeStatus.COMPLETED,
    ):
        raise problem(status.HTTP_409_CONFLICT, "The debate isn’t running.", "not_live")
    if body.speakerId != str(student.id) or not 0 <= body.slotIndex < len(challenge.schedule["slots"]):
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That turn doesn’t belong to you.", "invalid_turn"
        )
    if await session.get(LiveResult, challenge.id):
        raise problem(status.HTTP_409_CONFLICT, "The debate has already been judged.", "judged")
    mine = _side_of(challenge, student.id)
    slot = challenge.schedule["slots"][body.slotIndex]
    crossex = slot["phase"] == "crossex"
    if not crossex and slot["side"] != mine:
        raise problem(status.HTTP_403_FORBIDDEN, "That turn wasn’t yours.", "not_your_turn")
    role = ("question" if slot["side"] == mine else "answer") if crossex else "speech"
    payload = body.model_dump() | {
        "side": mine,
        "phase": slot["phase"],
        "role": role,
        "allowedMs": int(slot["duration_ms"]),
        "usedMs": min(body.usedMs, int(slot["duration_ms"]) + int(challenge.schedule.get("grace_ms", 5000))),
    }
    # One turn per student per slot: re-sending replaces your own turn, never anyone else's.
    await session.execute(
        delete(LiveEvent).where(
            LiveEvent.challenge_id == challenge.id,
            LiveEvent.kind == "turn",
            LiveEvent.user_id == student.id,
            LiveEvent.slot == body.slotIndex,
        )
    )
    await _append(session, challenge, "turn", student.id, body.slotIndex, payload)
    await session.commit()
    _forget_room(challenge.id)


@router.get("/live/{challenge_id}/audio")
async def audio_token(challenge_id: uuid.UUID, student: CurrentStudent, session: Session) -> dict[str, str]:
    """A LiveKit token for live voice in this room (installed app only; captions work everywhere)."""
    challenge = await _mine(session, challenge_id, student.id)
    if challenge.status != ChallengeStatus.LIVE:
        raise problem(status.HTTP_409_CONFLICT, "The debate isn’t running.", "not_live")
    token = service.livekit_token(
        room=f"sym-live-{challenge.id}", identity=str(student.id), name=student.user.full_name
    )
    if token is None:
        raise problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Live voice isn’t set up; captions only.",
            "audio_unavailable",
        )
    return token


# ---------- Judging and results ----------


async def _store_result(
    session: Session,
    challenge: Challenge,
    judge: dict[str, Any],
    reports: dict[str, Any],
    by: str,
    turns: list[dict[str, Any]],
    background: BackgroundTasks | None = None,
) -> LiveResult:
    assert challenge.opponent_id is not None
    sides = {str(uid): _side_of(challenge, uid) for uid in (challenge.challenger_id, challenge.opponent_id)}
    inserted = await session.execute(
        insert(LiveResult)
        .values(challenge_id=challenge.id, judge=judge, reports=reports, sides=sides, judged_by=by)
        .on_conflict_do_nothing(index_elements=[LiveResult.challenge_id])
        .returning(LiveResult.challenge_id)
    )
    created = inserted.first() is not None
    _forget_room(challenge.id)
    if created:  # first writer wins; only they save to the website tables
        challenge.status = ChallengeStatus.COMPLETED
        challenge.ended_at = challenge.ended_at or utcnow()
        challenge.web_debate_id = await service.save_to_web(session, challenge, turns, judge)
    await session.commit()
    result = await session.get(LiveResult, challenge.id)
    assert result is not None
    if created and background is not None:
        alerts.send(
            background,
            "result.ready",
            [challenge.challenger_id, challenge.opponent_id],
            challenge=challenge,
            actor="",
        )
    return result


def _ready_to_judge(challenge: Challenge, events: list[LiveEvent], now_ms: int) -> bool:
    if challenge.schedule is None or challenge.started_at is None:
        return False
    clock = service.clock_now(challenge, events, now_ms)
    if clock.stage != "done":
        return False
    turns = [event.payload for event in events if event.kind == "turn"]
    # Every slot has its owner's turn (or a late transcript had a minute to arrive).
    owners = {
        (slot["index"], slot["side"]) for slot in challenge.schedule["slots"] if slot["phase"] != "crossex"
    }
    have = {(turn["slotIndex"], turn["side"]) for turn in turns}
    finished = done_at_ms(
        slots=service.slots_of(challenge.schedule),
        started_at_ms=service.ms(challenge.started_at),
        events=service.clock_events(events),
        grace_ms=challenge.schedule["grace_ms"],
        timeout_ms=challenge.schedule["timeout_ms"],
    )
    left = any(event.kind == "leave" for event in events)
    return left or owners <= have or now_ms >= finished + JUDGE_WAIT_MS


@router.post("/live/{challenge_id}/judge")
async def judge_room(
    challenge_id: uuid.UUID, student: CurrentStudent, session: Session, background: BackgroundTasks
) -> dict[str, Any]:
    """Judge once the debate is over. 503 `ai_unavailable` → the app judges on the same turns and posts it."""
    challenge = await _mine(session, challenge_id, student.id)
    if await session.get(LiveResult, challenge.id) is None:
        events = await service.room_events(session, challenge.id)
        if not _ready_to_judge(challenge, events, int(time.time() * 1000)):
            raise problem(status.HTTP_409_CONFLICT, "Waiting for the last turn.", "not_finished")
        assert challenge.opponent_id is not None
        turns = [event.payload for event in events if event.kind == "turn"]
        parsed = [live_judge.Turn.model_validate(turn) for turn in turns]
        who = await service.people(session, {challenge.challenger_id, challenge.opponent_id})
        names = {str(uid): person["name"] for uid, person in who.items()}
        reports: dict[str, Any]
        try:
            judged = await live_judge.judge(
                motion=challenge.topic.get("title", ""),
                turns=parsed,
                debaters={str(challenge.challenger_id): "A", str(challenge.opponent_id): "B"},
                user_id=student.id,
                names=names,
            )
            verdict, reports, by = judged["judge"], judged["reports"], "ai"
        except AiUnavailable:
            # The built-in judge, on the server: each student's phone writes their own private report.
            verdict = fallback.judge_debate(
                parsed,
                [
                    (str(challenge.challenger_id), names.get(str(challenge.challenger_id), "Debater A")),
                    (str(challenge.opponent_id), names.get(str(challenge.opponent_id), "Debater B")),
                ],
            )
            reports, by = {}, "app"
        leaver = _early_leaver(challenge, events)
        if leaver is not None:
            verdict = fallback.apply_forfeit(verdict, leaver_id=str(leaver), names=names)
        await _store_result(session, challenge, verdict, reports, by, turns, background)
    return await my_result(challenge_id, student, session)


def _early_leaver(challenge: Challenge, events: list[LiveEvent]) -> int | None:
    """Who left before the debate's natural end, if anyone (they lose by forfeit)."""
    leaves = [event for event in events if event.kind == "leave"]
    if not leaves or challenge.schedule is None or challenge.started_at is None:
        return None
    first = min(leaves, key=lambda event: event.created_at)
    natural_end = done_at_ms(
        slots=service.slots_of(challenge.schedule),
        started_at_ms=service.ms(challenge.started_at),
        events=[event for event in service.clock_events(events) if event.kind != "leave"],
        grace_ms=challenge.schedule["grace_ms"],
        timeout_ms=challenge.schedule["timeout_ms"],
    )
    return first.user_id if service.ms(first.created_at) < natural_end else None


class MyReportIn(BaseModel):
    report: dict[str, Any]

    @field_validator("report")
    @classmethod
    def _small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > 40_000:
            raise ValueError("That report is too large.")
        return value


@router.put("/live/{challenge_id}/my-report")
async def save_my_report(
    challenge_id: uuid.UUID, body: MyReportIn, student: CurrentStudent, session: Session
) -> dict[str, Any]:
    """Your private report, written on your phone when the built-in judge decided. Only ever your own."""
    challenge = await _mine(session, challenge_id, student.id)
    result = await session.get(LiveResult, challenge.id, with_for_update=True)
    if result is None:
        raise problem(status.HTTP_404_NOT_FOUND, "Not judged yet.", "not_found")
    me = str(student.id)
    if result.reports.get(me) is None:
        result.reports = {**result.reports, me: body.report | {"userId": me}}
        await session.commit()
    return await my_result(challenge_id, student, session)


@router.get("/live/{challenge_id}/result")
async def my_result(challenge_id: uuid.UUID, student: CurrentStudent, session: Session) -> dict[str, Any]:
    """The verdict and transcript (shared) and only your own report (private)."""
    challenge = await _mine(session, challenge_id, student.id)
    result = await session.get(LiveResult, challenge.id)
    if result is None:
        raise problem(status.HTTP_404_NOT_FOUND, "Not judged yet.", "not_found")
    events = await service.room_events(session, challenge.id)
    me = str(student.id)
    return {
        "id": str(challenge.id),
        "challenge": (await _out(session, challenge))[0],
        "sides": result.sides,
        "turns": [event.payload for event in events if event.kind == "turn"],
        "judge": result.judge,
        "reports": {me: result.reports.get(me)},
        "judgedBy": result.judged_by,
        "completedAt": result.completed_at.isoformat(),
    }


@router.get("/live/history")
async def history(student: CurrentStudent, session: Session) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Challenge)
        .join(LiveResult, LiveResult.challenge_id == Challenge.id)
        .where(or_(Challenge.challenger_id == student.id, Challenge.opponent_id == student.id))
        .order_by(LiveResult.completed_at.desc())
        .limit(50)
    )
    return [await my_result(challenge.id, student, session) for challenge in rows]


# ---------- Safety ----------


class ReportIn(BaseModel):
    reason: Literal["abuse", "harassment", "inappropriate", "cheating", "other"]
    details: str = Field("", max_length=1000)
    challenge_id: uuid.UUID | None = None


@router.post("/users/{user_id}/report", status_code=status.HTTP_201_CREATED)
async def report_user(
    user_id: int, body: ReportIn, student: CurrentStudent, session: Session
) -> dict[str, str]:
    await ratelimit.hit(f"report:{student.id}", limit=20, per_seconds=24 * 60 * 60)
    if user_id == student.id or await session.get(Profile, user_id) is None:
        raise problem(status.HTTP_404_NOT_FOUND, "Student not found.", "not_found")
    if body.challenge_id is not None:
        challenge = await session.get(Challenge, body.challenge_id)
        if challenge is None or {student.id, user_id} != {challenge.challenger_id, challenge.opponent_id}:
            raise problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "That debate isn’t between you two.", "invalid_report"
            )
    session.add(
        UserReport(
            reporter_id=student.id,
            reported_id=user_id,
            challenge_id=body.challenge_id,
            reason=body.reason,
            details=body.details.strip(),
        )
    )
    await session.commit()
    return {"message": "Thanks — the SYM team will review this. You can also block this student."}


@router.post("/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def block_user(user_id: int, student: CurrentStudent, session: Session) -> None:
    """Blocking hides both of you from each other's search and cancels challenges between you."""
    if user_id == student.id:
        raise problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "You can’t block yourself.", "invalid")
    await ratelimit.hit(f"block:{student.id}", limit=50, per_seconds=24 * 60 * 60)
    if await session.get(Profile, user_id) is None:
        raise problem(status.HTTP_404_NOT_FOUND, "Student not found.", "not_found")
    await session.execute(
        insert(Block).values(blocker_id=student.id, blocked_id=user_id).on_conflict_do_nothing()
    )
    await session.execute(
        update(Challenge)
        .where(
            Challenge.status.in_(
                [ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED, ChallengeStatus.COUNTERED]
            ),
            or_(
                (Challenge.challenger_id == student.id) & (Challenge.opponent_id == user_id),
                (Challenge.challenger_id == user_id) & (Challenge.opponent_id == student.id),
            ),
        )
        .values(status=ChallengeStatus.CANCELLED)
    )
    await session.commit()


@router.delete("/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_user(user_id: int, student: CurrentStudent, session: Session) -> None:
    await session.execute(delete(Block).where(Block.blocker_id == student.id, Block.blocked_id == user_id))
    await session.commit()


@router.get("/blocks")
async def my_blocks(student: CurrentStudent, session: Session) -> list[PersonOut]:
    ids = set(
        await session.scalars(
            select(Block.blocked_id)
            .join(Profile, Profile.user_id == Block.blocked_id)
            .where(Block.blocker_id == student.id)
        )
    )
    who = await service.people(session, ids)
    return [PersonOut(**person) for person in who.values()]
