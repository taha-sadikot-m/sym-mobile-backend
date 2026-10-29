"""Fixed-window rate limits kept in Postgres (no Redis needed at this scale).

Uses its own short transaction, so counting an attempt never commits or rolls back the caller's work.
"""

from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, text

from app.core.db import SessionLocal
from app.models import RateLimit
from app.models.base import utcnow


def _window(now: datetime, seconds: int) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=now.tzinfo)


async def hit(key: str, *, limit: int, per_seconds: int) -> None:
    """Count one attempt for `key`; raise 429 once more than `limit` happened in the current window."""
    async with SessionLocal() as session:
        count = await session.scalar(
            text(
                """
                INSERT INTO mobile.rate_limits (key, window_start, count) VALUES (:key, :window, 1)
                ON CONFLICT (key, window_start) DO UPDATE SET count = mobile.rate_limits.count + 1
                RETURNING count
                """
            ),
            {"key": key[:200], "window": _window(utcnow(), per_seconds)},
        )
        await session.commit()
    if count is not None and count > limit:
        minutes = max(1, per_seconds // 60)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        )


async def clear(key: str) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(RateLimit).where(RateLimit.key == key[:200]))
        await session.commit()


async def prune(older_than: timedelta = timedelta(days=2)) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(RateLimit).where(RateLimit.window_start < utcnow() - older_than))
        await session.commit()
