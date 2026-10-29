"""Live debates and moderation in the admin console."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core import audit
from app.models import AdminRole, AdminUser, AppStatus, Profile
from app.models.base import utcnow
from app.models.live import Challenge, ChallengeStatus, LiveEvent, LiveResult, ReportStatus, UserReport
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header, require_role
from app.modules.admin_content.common import PageOut, conflict, not_found
from app.modules.auth import service as auth
from app.modules.live import service as live

router = APIRouter(prefix="/admin/api", tags=["admin live"], dependencies=[Depends(require_admin_header)])

Moderator = Annotated[AdminUser, Depends(require_role(AdminRole.MODERATOR))]


class ChallengeRow(BaseModel):
    id: uuid.UUID
    code: str
    topic: str
    format: str
    status: str
    challenger: str
    opponent: str | None
    scheduled_at: datetime
    created_at: datetime
    winner: str | None


@router.get("/live/challenges")
async def list_challenges(
    _: CurrentAdmin,
    session: DbSession,
    status_filter: Annotated[str | None, Query(alias="status", max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageOut[ChallengeRow]:
    stmt = select(Challenge)
    if status_filter:
        stmt = stmt.where(Challenge.status == status_filter)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await session.scalars(stmt.order_by(Challenge.scheduled_at.desc()).limit(limit).offset(offset))
    )
    who = await live.people(
        session, {c.challenger_id for c in rows} | {c.opponent_id for c in rows if c.opponent_id}
    )
    results = {
        result.challenge_id: result
        for result in await session.scalars(
            select(LiveResult).where(LiveResult.challenge_id.in_([c.id for c in rows]))
        )
    }

    def name(user_id: int | None) -> str | None:
        return who[user_id]["name"] if user_id in who else None

    items = [
        ChallengeRow(
            id=c.id,
            code=c.code,
            topic=c.topic.get("title", ""),
            format=c.format,
            status=c.status,
            challenger=name(c.challenger_id) or "?",
            opponent=name(c.opponent_id),
            scheduled_at=c.scheduled_at,
            created_at=c.created_at,
            winner=name(int(results[c.id].judge["winnerId"])) if c.id in results else None,
        )
        for c in rows
    ]
    return PageOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/live/challenges/{challenge_id}")
async def challenge_detail(challenge_id: uuid.UUID, _: Moderator, session: DbSession) -> dict[str, Any]:
    """Everything about one debate, including both private reports (moderators only)."""
    challenge = await session.get(Challenge, challenge_id)
    if challenge is None:
        raise not_found("Debate")
    events = list(
        await session.scalars(
            select(LiveEvent).where(LiveEvent.challenge_id == challenge.id).order_by(LiveEvent.seq)
        )
    )
    who = await live.people(
        session, {challenge.challenger_id} | ({challenge.opponent_id} if challenge.opponent_id else set())
    )
    result = await session.get(LiveResult, challenge.id)
    return {
        "challenge": live.serialize(challenge, who),
        "turns": [event.payload for event in events if event.kind == "turn"],
        "events": [
            {
                "kind": e.kind,
                "slot": e.slot,
                "user": who.get(e.user_id, {}).get("name"),
                "at": e.created_at.isoformat(),
            }
            for e in events
            if e.kind != "turn"
        ],
        "result": {"judge": result.judge, "reports": result.reports, "judged_by": result.judged_by}
        if result
        else None,
        "web_debate_id": str(challenge.web_debate_id) if challenge.web_debate_id else None,
    }


@router.post("/live/challenges/{challenge_id}/cancel")
async def cancel_challenge(
    challenge_id: uuid.UUID, admin: Moderator, request: Request, session: DbSession
) -> dict[str, str]:
    challenge = await session.get(Challenge, challenge_id)
    if challenge is None:
        raise not_found("Debate")
    if challenge.status == ChallengeStatus.COMPLETED:
        raise conflict("This debate has already finished.")
    challenge.status = ChallengeStatus.CANCELLED
    challenge.ended_at = challenge.ended_at or utcnow()
    audit.record(
        session,
        "live.cancelled",
        admin_id=admin.id,
        target_type="challenge",
        target_id=str(challenge.id),
        ip=client_ip(request),
    )
    await session.commit()
    return {"status": "cancelled"}


# ---------- Moderation ----------


class ReportRow(BaseModel):
    id: uuid.UUID
    reason: str
    details: str
    status: ReportStatus
    reporter: dict[str, str]
    reported: dict[str, str]
    reported_app_status: str | None
    reports_against_reported: int
    challenge_id: uuid.UUID | None
    created_at: datetime
    resolution_note: str | None


class ResolveIn(BaseModel):
    action: Literal["dismiss", "actioned"]
    note: str = Field("", max_length=1000)
    pause_app_access: bool = False


@router.get("/moderation/reports")
async def list_reports(
    _: Moderator,
    session: DbSession,
    status_filter: Annotated[ReportStatus | None, Query(alias="status")] = ReportStatus.OPEN,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageOut[ReportRow]:
    stmt = select(UserReport)
    if status_filter:
        stmt = stmt.where(UserReport.status == status_filter)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await session.scalars(stmt.order_by(UserReport.created_at.desc()).limit(limit).offset(offset))
    )
    ids = {r.reporter_id for r in rows} | {r.reported_id for r in rows}
    who = await live.people(session, ids)
    counts = {
        user_id: count
        for user_id, count in await session.execute(
            select(UserReport.reported_id, func.count())
            .where(UserReport.reported_id.in_({r.reported_id for r in rows}))
            .group_by(UserReport.reported_id)
        )
    }
    statuses = {
        profile.user_id: profile.app_status
        for profile in await session.scalars(
            select(Profile).where(Profile.user_id.in_({r.reported_id for r in rows}))
        )
    }
    unknown = {"id": "", "name": "Deleted account", "username": "", "school": ""}
    items = [
        ReportRow(
            id=r.id,
            reason=r.reason,
            details=r.details,
            status=r.status,
            reporter=who.get(r.reporter_id, unknown),
            reported=who.get(r.reported_id, unknown),
            reported_app_status=statuses.get(r.reported_id),
            reports_against_reported=counts.get(r.reported_id, 0),
            challenge_id=r.challenge_id,
            created_at=r.created_at,
            resolution_note=r.resolution_note,
        )
        for r in rows
    ]
    return PageOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/moderation/open-count")
async def open_count(_: CurrentAdmin, session: DbSession) -> dict[str, int]:
    count = await session.scalar(select(func.count()).where(UserReport.status == ReportStatus.OPEN))
    return {"open": count or 0}


@router.post("/moderation/reports/{report_id}/resolve")
async def resolve_report(
    report_id: uuid.UUID, body: ResolveIn, admin: Moderator, request: Request, session: DbSession
) -> dict[str, str]:
    report = await session.get(UserReport, report_id)
    if report is None:
        raise not_found("Report")
    report.status = ReportStatus.DISMISSED if body.action == "dismiss" else ReportStatus.ACTIONED
    report.resolution_note = body.note.strip() or None
    report.resolved_by = admin.id
    report.resolved_at = utcnow()
    if body.pause_app_access and body.action == "actioned":
        profile = await session.get(Profile, report.reported_id)
        if profile is not None and profile.app_status != AppStatus.SUSPENDED:
            profile.app_status = AppStatus.SUSPENDED
            profile.suspended_reason = f"Report: {report.reason}. {body.note}".strip()
            await auth.revoke_all(session, report.reported_id)
            audit.record(
                session,
                "student.suspended",
                admin_id=admin.id,
                target_type="student",
                target_id=str(report.reported_id),
                ip=client_ip(request),
                reason=profile.suspended_reason,
            )
    audit.record(
        session,
        f"report.{report.status}",
        admin_id=admin.id,
        target_type="report",
        target_id=str(report.id),
        ip=client_ip(request),
        reason=report.reason,
        note=body.note.strip(),
    )
    await session.commit()
    return {"status": report.status}
