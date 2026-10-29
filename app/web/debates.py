"""The web backend's debate tables, so debates played in the app appear in the student's website history.

Rows are written the way the web's Django code writes them (see apps/debates in the web backend):
practice debates are mode PRACTICE with an AI opponent, the result's totals are the sum of the three
scores, the winner is the student only when they won (otherwise empty, which the web counts as a draw),
and `user_debate_stats` is updated with the same running-average rules. Every row is tagged
`metadata.source = "mobile"`.
"""

import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow

web = MetaData(schema="public")

debate_sessions = Table(
    "debate_sessions",
    web,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("mode", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("is_ai_opponent", Boolean, nullable=False),
    Column("topic", Text, nullable=False),
    Column("topic_type", String(20), nullable=False),
    Column("creator_position", String(20), nullable=False),
    Column("opponent_position", String(20), nullable=False),
    Column("difficulty", String(20), nullable=False),
    Column("opening_speaker", String(20), nullable=False),
    Column("total_rounds", Integer, nullable=False),
    Column("current_round", Integer, nullable=False),
    Column("time_per_round", Integer, nullable=False),
    Column("current_turn", String(20), nullable=False),
    Column("scheduled_at", DateTime(timezone=True)),
    Column("started_at", DateTime(timezone=True)),
    Column("ended_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("creator_id", BigInteger, nullable=False),
    Column("event_id", UUID(as_uuid=True)),
    Column("opponent_id", BigInteger),
    Column("organization_id", BigInteger),
)

debate_messages = Table(
    "debate_messages",
    web,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("is_ai_message", Boolean, nullable=False),
    Column("message_type", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("audio_file", String(100)),
    Column("audio_duration", Integer),
    Column("round_number", Integer, nullable=False),
    Column("sequence_number", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("debate_id", UUID(as_uuid=True), nullable=False),
    Column("sender_id", BigInteger),
)

debate_results = Table(
    "debate_results",
    web,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("creator_logical_score", Numeric(5, 2), nullable=False),
    Column("creator_emotional_score", Numeric(5, 2), nullable=False),
    Column("creator_rebuttal_score", Numeric(5, 2), nullable=False),
    Column("creator_total_score", Numeric(6, 2), nullable=False),
    Column("opponent_logical_score", Numeric(5, 2), nullable=False),
    Column("opponent_emotional_score", Numeric(5, 2), nullable=False),
    Column("opponent_rebuttal_score", Numeric(5, 2), nullable=False),
    Column("opponent_total_score", Numeric(6, 2), nullable=False),
    Column("judged_by", String(20), nullable=False),
    Column("judge_notes", Text, nullable=False),
    Column("creator_rank_change", Integer, nullable=False),
    Column("opponent_rank_change", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("debate_id", UUID(as_uuid=True), nullable=False),
    Column("winner_id", BigInteger),
)

user_debate_stats = Table(
    "user_debate_stats",
    web,
    Column("user_id", BigInteger, primary_key=True),
    Column("total_debates", Integer, nullable=False),
    Column("total_wins", Integer, nullable=False),
    Column("total_losses", Integer, nullable=False),
    Column("total_draws", Integer, nullable=False),
    Column("practice_debates", Integer, nullable=False),
    Column("pvp_debates", Integer, nullable=False),
    Column("event_debates", Integer, nullable=False),
    Column("avg_logical_score", Numeric(5, 2), nullable=False),
    Column("avg_emotional_score", Numeric(5, 2), nullable=False),
    Column("avg_rebuttal_score", Numeric(5, 2), nullable=False),
    Column("avg_total_score", Numeric(6, 2), nullable=False),
    Column("current_rank", Integer, nullable=False),
    Column("peak_rank", Integer, nullable=False),
    Column("rank_tier", String(20), nullable=False),
    Column("current_win_streak", Integer, nullable=False),
    Column("longest_win_streak", Integer, nullable=False),
    Column("last_debate_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _two(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def exists(session: AsyncSession, debate_id: uuid.UUID) -> bool:
    return (
        await session.scalar(select(debate_sessions.c.id).where(debate_sessions.c.id == debate_id))
        is not None
    )


async def record_debate(
    session: AsyncSession,
    *,
    debate_id: uuid.UUID,
    mode: str,
    creator_id: int,
    opponent_id: int | None,
    topic: str,
    creator_for: bool,
    difficulty: str,
    started_at: datetime,
    ended_at: datetime,
    messages: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    """Insert a finished debate and its messages (the caller commits)."""
    rounds = max((message["round"] for message in messages), default=1)
    await session.execute(
        insert(debate_sessions).values(
            id=debate_id,
            mode=mode,
            status="COMPLETED",
            is_ai_opponent=opponent_id is None,
            topic=topic,
            topic_type="TOPIC",
            creator_position="PRO" if creator_for else "CON",
            opponent_position="CON" if creator_for else "PRO",
            difficulty=difficulty,
            # For opens every phase in SYM formats.
            opening_speaker="CREATOR" if creator_for else "OPPONENT",
            total_rounds=rounds,
            current_round=rounds,
            time_per_round=120,
            current_turn="CREATOR",
            scheduled_at=None,
            started_at=started_at,
            ended_at=ended_at,
            created_at=started_at,
            updated_at=utcnow(),
            metadata={"source": "mobile", **metadata},
            creator_id=creator_id,
            event_id=None,
            opponent_id=opponent_id,
            organization_id=None,
        )
    )
    for sequence, message in enumerate(messages, start=1):
        await session.execute(
            insert(debate_messages).values(
                id=message.get("id") or uuid.uuid4(),
                is_ai_message=message["sender_id"] is None and opponent_id is None,
                message_type="TEXT",
                content=message["text"],
                audio_file=None,
                audio_duration=round(message["duration_ms"] / 1000) if message.get("duration_ms") else None,
                round_number=message["round"],
                sequence_number=sequence,
                created_at=message.get("created_at") or ended_at,
                metadata={
                    "source": "mobile",
                    "phase": message.get("phase"),
                    "input": message.get("input", "text"),
                },
                debate_id=debate_id,
                sender_id=message["sender_id"],
            )
        )


async def record_result(
    session: AsyncSession,
    *,
    debate_id: uuid.UUID,
    winner_id: int | None,
    creator: tuple[float, float, float],
    opponent: tuple[float, float, float],
    judge_notes: str,
    metadata: dict[str, Any],
) -> None:
    creator_scores = [_two(value) for value in creator]
    opponent_scores = [_two(value) for value in opponent]
    await session.execute(
        insert(debate_results).values(
            id=uuid.uuid4(),
            creator_logical_score=creator_scores[0],
            creator_emotional_score=creator_scores[1],
            creator_rebuttal_score=creator_scores[2],
            creator_total_score=sum(creator_scores),
            opponent_logical_score=opponent_scores[0],
            opponent_emotional_score=opponent_scores[1],
            opponent_rebuttal_score=opponent_scores[2],
            opponent_total_score=sum(opponent_scores),
            judged_by="AI",
            judge_notes=judge_notes,
            creator_rank_change=0,
            opponent_rank_change=0,
            created_at=utcnow(),
            metadata={"source": "mobile", **metadata},
            debate_id=debate_id,
            winner_id=winner_id,
        )
    )


async def update_stats(
    session: AsyncSession,
    *,
    user_id: int,
    mode: str,
    winner_id: int | None,
    scores: tuple[float, float, float],
) -> None:
    """The web's UserDebateStats.update_stats_after_debate, row-locked so both backends can't clash."""
    row = (
        await session.execute(
            select(user_debate_stats).where(user_debate_stats.c.user_id == user_id).with_for_update()
        )
    ).first()
    now = utcnow()
    if row is None:
        await session.execute(
            insert(user_debate_stats).values(
                user_id=user_id,
                total_debates=0,
                total_wins=0,
                total_losses=0,
                total_draws=0,
                practice_debates=0,
                pvp_debates=0,
                event_debates=0,
                avg_logical_score=0,
                avg_emotional_score=0,
                avg_rebuttal_score=0,
                avg_total_score=0,
                current_rank=1000,
                peak_rank=1000,
                rank_tier="UNRANKED",
                current_win_streak=0,
                longest_win_streak=0,
                last_debate_at=None,
                updated_at=now,
            )
        )
        row = (
            await session.execute(
                select(user_debate_stats).where(user_debate_stats.c.user_id == user_id).with_for_update()
            )
        ).one()
    stats = row._mapping
    total = stats["total_debates"] + 1
    values: dict[str, Any] = {"total_debates": total, "last_debate_at": now, "updated_at": now}
    values[{"PRACTICE": "practice_debates", "PVP": "pvp_debates", "EVENT": "event_debates"}[mode]] = (
        stats[{"PRACTICE": "practice_debates", "PVP": "pvp_debates", "EVENT": "event_debates"}[mode]] + 1
    )
    if winner_id == user_id:
        streak = stats["current_win_streak"] + 1
        values |= {
            "total_wins": stats["total_wins"] + 1,
            "current_win_streak": streak,
            "longest_win_streak": max(stats["longest_win_streak"], streak),
        }
    elif winner_id is None:
        values |= {"total_draws": stats["total_draws"] + 1, "current_win_streak": 0}
    else:
        values |= {"total_losses": stats["total_losses"] + 1, "current_win_streak": 0}

    def running(old: Decimal, new: float) -> Decimal:
        return _two((float(old) * (total - 1) + new) / total)

    logical, emotional, rebuttal = scores
    values |= {
        "avg_logical_score": running(stats["avg_logical_score"], logical),
        "avg_emotional_score": running(stats["avg_emotional_score"], emotional),
        "avg_rebuttal_score": running(stats["avg_rebuttal_score"], rebuttal),
        "avg_total_score": running(stats["avg_total_score"], logical + emotional + rebuttal),
    }
    await session.execute(
        update(user_debate_stats).where(user_debate_stats.c.user_id == user_id).values(**values)
    )
