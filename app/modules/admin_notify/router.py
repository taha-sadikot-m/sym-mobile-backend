"""Admin console: notifications (log, devices, announcements) and scheduled jobs (runs, run now)."""

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from app import jobs
from app.core import audit, ratelimit
from app.models import JobRun, Notification, PushToken
from app.models.base import utcnow
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import PageOut, SuperAdmin
from app.notify.push import notify

router = APIRouter(
    prefix="/admin/api", tags=["admin notifications"], dependencies=[Depends(require_admin_header)]
)


class NotificationRow(BaseModel):
    id: uuid.UUID
    user_id: int | None
    kind: str
    title: str
    body: str
    devices: int
    delivered: int
    error: str | None
    created_at: datetime


class NotificationsOut(PageOut[NotificationRow]):
    devices: int
    students_with_devices: int
    sent_week: int
    failed_week: int


@router.get("/notifications")
async def list_notifications(
    _: CurrentAdmin,
    session: DbSession,
    kind: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationsOut:
    stmt = select(Notification).order_by(Notification.created_at.desc())
    if kind:
        stmt = stmt.where(Notification.kind == kind)
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(await session.scalars(stmt.limit(limit).offset(offset)))
    week = utcnow() - timedelta(days=7)
    sent, failed = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(case((Notification.error.is_not(None), 1), else_=0)), 0)
            ).where(Notification.created_at >= week)
        )
    ).one()
    return NotificationsOut(
        items=[NotificationRow.model_validate(row, from_attributes=True) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        devices=await session.scalar(select(func.count()).select_from(PushToken)) or 0,
        students_with_devices=await session.scalar(select(func.count(func.distinct(PushToken.user_id)))) or 0,
        sent_week=int(sent),
        failed_week=int(failed),
    )


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=3, max_length=60)
    body: str = Field(min_length=3, max_length=180)


@router.post("/notifications/announce")
async def announce(
    body: AnnouncementIn, admin: SuperAdmin, request: Request, session: DbSession
) -> dict[str, str]:
    """Send a notification to every student who has the app with notifications on."""
    await ratelimit.hit(f"announce:{admin.id}", limit=5, per_seconds=24 * 60 * 60)
    audit.record(
        session,
        "notification.announced",
        admin_id=admin.id,
        target_type="notification",
        ip=client_ip(request),
        title=body.title,
        body=body.body,
    )
    await session.commit()
    await notify(None, kind="announcement", title=body.title, body=body.body, data={"type": "announcement"})
    return {"message": "Announcement sent."}


class JobRunOut(BaseModel):
    id: uuid.UUID
    name: str
    started_at: datetime
    finished_at: datetime | None
    ok: bool
    details: dict[str, Any]
    error: str | None


@router.get("/jobs")
async def list_jobs(_: CurrentAdmin, session: DbSession) -> list[JobRunOut]:
    rows = await session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(40))
    return [JobRunOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("/jobs/run")
async def run_jobs(admin: SuperAdmin, request: Request, session: DbSession) -> list[JobRunOut]:
    """Run reminders and the clean-up now (the cron job normally does this every 5 minutes)."""
    await ratelimit.hit(f"jobs-run:{admin.id}", limit=20, per_seconds=60 * 60)
    audit.record(session, "jobs.run", admin_id=admin.id, target_type="job", ip=client_ip(request))
    await session.commit()
    runs = await jobs.run_due(force_cleanup=True)
    return [JobRunOut.model_validate(run, from_attributes=True) for run in runs]
