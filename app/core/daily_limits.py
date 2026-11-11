"""Free-plan daily limits (spec 8): 3 scored attempts and 20 Chanakya messages per student per India day.

Counted in `mobile.usage_daily` with one atomic upsert that only increments below the limit, so two
requests at once can never both take the last one. Uses its own short transaction, like rate limits. When
the AI then can't answer, the request gives its unit back (the app falls back on the phone, uncounted).
The Academy and Resources are unlimited and never come here.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.models import UsageDaily

Kind = Literal["scored_attempts", "chat_messages"]

LIMITS: dict[Kind, int] = {"scored_attempts": 3, "chat_messages": 20}

MESSAGES: dict[Kind, str] = {
    "scored_attempts": (
        "You’ve used today’s 3 scored attempts. Come back tomorrow — the Academy and Resources are open "
        "any time."
    ),
    "chat_messages": (
        "You’ve sent Chanakya 20 messages today. Come back tomorrow — the Academy and Resources are open "
        "any time."
    ),
}

INDIA = timezone(timedelta(hours=5, minutes=30), "IST")  # no daylight saving


def today() -> date:
    return datetime.now(UTC).astimezone(INDIA).date()


@dataclass(frozen=True)
class Ticket:
    """One unit taken from today's allowance; `remaining` is what's left after it."""

    user_id: int
    kind: Kind
    day: date
    remaining: int


async def take(user_id: int, kind: Kind) -> Ticket:
    """Count one use for today; raise 429 `daily_limit` (counting nothing) once the limit is reached."""
    limit = LIMITS[kind]
    day = today()
    column = getattr(UsageDaily, kind)
    values = {"user_id": user_id, "day": day, "scored_attempts": 0, "chat_messages": 0} | {kind: 1}
    stmt = (
        pg_insert(UsageDaily)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[UsageDaily.user_id, UsageDaily.day],
            set_={kind: column + 1},
            where=column < limit,
        )
        .returning(column)
    )
    async with SessionLocal() as session:
        used = await session.scalar(stmt)
        await session.commit()
    if used is None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"message": MESSAGES[kind], "code": "daily_limit", "kind": kind, "limit": limit},
        )
    return Ticket(user_id=user_id, kind=kind, day=day, remaining=max(0, limit - used))


async def give_back(ticket: Ticket) -> None:
    """Undo `take` (the AI couldn't answer, so the attempt didn't happen on the server)."""
    column = getattr(UsageDaily, ticket.kind)
    async with SessionLocal() as session:
        await session.execute(
            update(UsageDaily)
            .where(UsageDaily.user_id == ticket.user_id, UsageDaily.day == ticket.day, column > 0)
            .values({ticket.kind: column - 1})
        )
        await session.commit()


async def used_today(user_id: int) -> dict[Kind, int]:
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(UsageDaily.scored_attempts, UsageDaily.chat_messages).where(
                    UsageDaily.user_id == user_id, UsageDaily.day == today()
                )
            )
        ).first()
    return {"scored_attempts": row[0] if row else 0, "chat_messages": row[1] if row else 0}
