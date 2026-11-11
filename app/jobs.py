"""Scheduled work: debate reminders and data clean-up.

Run every 5 minutes by Render's cron job (`python -m app.jobs`), or from the admin console's System page.
Each job records a row in mobile.job_runs. The clean-up runs at most once a day.

What the clean-up removes (the retention rules in docs/DATA_AND_PRIVACY.md):
- sign-in codes a day after they expire; sign-in sessions 30 days after they end
- rate-limit counters after 2 days; admin sessions 30 days after they end
- notification log after 90 days; AI usage log after 400 days; daily free-limit counters after 30 days
- unanswered challenges are marked expired once their 48 hours pass
No audio is ever stored, so there is none to delete.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update

from app.core.db import SessionLocal, engine
from app.models import (
    AdminSession,
    AiCall,
    EmailCode,
    JobRun,
    Notification,
    RateLimit,
    RefreshToken,
    UsageDaily,
)
from app.models.base import utcnow
from app.models.live import Challenge, ChallengeStatus
from app.modules.live import alerts
from app.notify.push import notify

log = logging.getLogger("sym.jobs")

REMIND_BEFORE = timedelta(minutes=15)
CLEANUP_EVERY = timedelta(hours=23)


async def send_reminders() -> dict[str, Any]:
    """Remind both students 15 minutes before an accepted debate (once per scheduled time)."""
    now = utcnow()
    async with SessionLocal() as session:
        due = list(
            await session.scalars(
                select(Challenge)
                .where(
                    Challenge.status == ChallengeStatus.ACCEPTED,
                    Challenge.reminder.is_(True),
                    Challenge.reminded_at.is_(None),
                    Challenge.scheduled_at > now,
                    Challenge.scheduled_at <= now + REMIND_BEFORE,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for challenge in due:
            challenge.reminded_at = now
        await session.commit()
    for challenge in due:
        title, body = alerts.message("challenge.reminder", challenge)
        await notify(
            [challenge.challenger_id, *([challenge.opponent_id] if challenge.opponent_id else [])],
            kind="challenge.reminder",
            title=title,
            body=body,
            data={"type": "challenge.reminder", "challengeId": str(challenge.id)},
        )
    return {"reminded": len(due)}


async def clean_up() -> dict[str, Any]:
    now = utcnow()
    counts: dict[str, Any] = {}
    async with SessionLocal() as session:
        steps = {
            "email_codes": delete(EmailCode).where(EmailCode.expires_at < now - timedelta(days=1)),
            "refresh_tokens": delete(RefreshToken).where(
                or_(
                    RefreshToken.expires_at < now - timedelta(days=30),
                    RefreshToken.revoked_at < now - timedelta(days=30),
                )
            ),
            "rate_limits": delete(RateLimit).where(RateLimit.window_start < now - timedelta(days=2)),
            "admin_sessions": delete(AdminSession).where(
                or_(
                    AdminSession.expires_at < now - timedelta(days=30),
                    AdminSession.revoked_at < now - timedelta(days=30),
                )
            ),
            "notifications": delete(Notification).where(Notification.created_at < now - timedelta(days=90)),
            "ai_calls": delete(AiCall).where(AiCall.created_at < now - timedelta(days=400)),
            "usage_daily": delete(UsageDaily).where(UsageDaily.day < (now - timedelta(days=30)).date()),
            "challenges_expired": update(Challenge)
            .where(
                Challenge.status.in_([ChallengeStatus.PENDING, ChallengeStatus.COUNTERED]),
                Challenge.expires_at < now,
            )
            .values(status=ChallengeStatus.EXPIRED),
        }
        for name, stmt in steps.items():
            result = await session.execute(stmt)
            counts[name] = getattr(result, "rowcount", 0)
        await session.commit()
    return counts


async def _record(name: str, work: Any) -> JobRun:
    async with SessionLocal() as session:
        run = JobRun(name=name)
        session.add(run)
        await session.commit()
        try:
            run.details = await work()
            run.ok = True
        except Exception as error:  # a failing job is recorded, then the next one still runs
            log.exception("Job %s failed", name)
            run.error = f"{type(error).__name__}: {str(error)[:500]}"
        run.finished_at = utcnow()
        await session.commit()
        await session.refresh(run)
        return run


async def run_due(*, force_cleanup: bool = False) -> list[JobRun]:
    runs = [await _record("reminders", send_reminders)]
    async with SessionLocal() as session:
        last = await session.scalar(
            select(JobRun.started_at)
            .where(JobRun.name == "cleanup", JobRun.ok.is_(True))
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )
    if force_cleanup or last is None or utcnow() - last >= CLEANUP_EVERY:
        runs.append(await _record("cleanup", clean_up))
    return runs


async def _main() -> list[JobRun]:
    try:
        return await run_due()
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    runs = asyncio.run(_main())
    for run in runs:
        log.info("%s: %s %s", run.name, "ok" if run.ok else "FAILED", run.details or run.error)
    if not all(run.ok for run in runs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
