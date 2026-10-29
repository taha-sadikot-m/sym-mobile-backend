"""Students in the admin console: app users (anyone with an app profile)."""

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Row, Select, func, or_, select

from app.core import audit
from app.models import AdminRole, AdminUser, AppStatus, CodePurpose, Profile, RefreshToken
from app.models.base import utcnow
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header, require_role
from app.modules.admin_content.common import PageOut, SuperAdmin, not_found
from app.modules.auth import service as auth
from app.modules.me.router import erase_student
from app.web import users as web_users
from app.web.users import users

router = APIRouter(
    prefix="/admin/api/students", tags=["admin students"], dependencies=[Depends(require_admin_header)]
)

Moderator = Annotated[AdminUser, Depends(require_role(AdminRole.MODERATOR))]


class StudentOut(BaseModel):
    id: int
    full_name: str
    email: str
    username: str | None
    age: int | None
    institution: str
    city: str
    country: str
    email_verified: bool
    account_active: bool
    app_status: AppStatus
    suspended_reason: str | None
    joined_at: datetime
    last_seen_at: datetime | None
    active_sessions: int = 0


class SuspendIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class StudentStats(BaseModel):
    total: int
    new_this_week: int
    active_this_week: int
    suspended: int


def _select() -> Select[Any]:
    return select(
        users.c.id,
        users.c.full_name,
        users.c.email,
        Profile.username,
        Profile.birth_year,
        Profile.institution,
        Profile.city,
        Profile.country,
        users.c.is_email_verified,
        users.c.is_active,
        Profile.app_status,
        Profile.suspended_reason,
        users.c.created_at,
        Profile.last_seen_at,
    ).join(users, users.c.id == Profile.user_id)


def _out(row: Row[Any], sessions: int = 0) -> StudentOut:
    data = row._mapping
    birth_year = data["birth_year"]
    return StudentOut(
        id=data["id"],
        full_name=data["full_name"],
        email=data["email"],
        username=data["username"],
        age=date.today().year - birth_year if birth_year else None,
        institution=data["institution"],
        city=data["city"],
        country=data["country"],
        email_verified=data["is_email_verified"],
        account_active=data["is_active"],
        app_status=data["app_status"],
        suspended_reason=data["suspended_reason"],
        joined_at=data["created_at"],
        last_seen_at=data["last_seen_at"],
        active_sessions=sessions,
    )


async def _get(session: DbSession, user_id: int) -> StudentOut:
    row = (await session.execute(_select().where(Profile.user_id == user_id))).first()
    if row is None:
        raise not_found("Student")
    sessions = await session.scalar(
        select(func.count(func.distinct(RefreshToken.family_id))).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.used_at.is_(None),
            RefreshToken.expires_at > utcnow(),
        )
    )
    return _out(row, sessions or 0)


@router.get("")
async def list_students(
    _: Moderator,
    session: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    app_status: AppStatus | None = None,
    verified: bool | None = None,
    sort: Literal["newest", "last_seen", "name"] = "newest",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageOut[StudentOut]:
    stmt = _select()
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                users.c.full_name.ilike(like),
                users.c.email.ilike(like),
                Profile.username.ilike(like.lstrip("@")),
                Profile.institution.ilike(like),
            )
        )
    if app_status:
        stmt = stmt.where(Profile.app_status == app_status)
    if verified is not None:
        stmt = stmt.where(users.c.is_email_verified.is_(verified))
    order = {
        "newest": users.c.created_at.desc(),
        "last_seen": Profile.last_seen_at.desc().nulls_last(),
        "name": users.c.full_name.asc(),
    }[sort]
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = await session.execute(stmt.order_by(order, users.c.id).limit(limit).offset(offset))
    return PageOut(items=[_out(row) for row in rows], total=total, limit=limit, offset=offset)


@router.get("/stats")
async def student_stats(_: CurrentAdmin, session: DbSession) -> StudentStats:
    week_ago = utcnow() - timedelta(days=7)
    joined = select(func.count()).select_from(Profile).join(users, users.c.id == Profile.user_id)
    return StudentStats(
        total=await session.scalar(joined) or 0,
        new_this_week=await session.scalar(joined.where(users.c.created_at >= week_ago)) or 0,
        active_this_week=await session.scalar(select(func.count()).where(Profile.last_seen_at >= week_ago))
        or 0,
        suspended=await session.scalar(select(func.count()).where(Profile.app_status == AppStatus.SUSPENDED))
        or 0,
    )


@router.get("/{user_id}")
async def get_student(user_id: int, _: Moderator, session: DbSession) -> StudentOut:
    return await _get(session, user_id)


@router.post("/{user_id}/suspend")
async def suspend(
    user_id: int, body: SuspendIn, admin: Moderator, request: Request, session: DbSession
) -> StudentOut:
    """Pause app access only; the student's website account is untouched."""
    profile = await session.get(Profile, user_id)
    if profile is None:
        raise not_found("Student")
    profile.app_status = AppStatus.SUSPENDED
    profile.suspended_reason = body.reason.strip()
    await auth.revoke_all(session, user_id)
    audit.record(
        session,
        "student.suspended",
        admin_id=admin.id,
        target_type="student",
        target_id=str(user_id),
        ip=client_ip(request),
        reason=profile.suspended_reason,
    )
    await session.commit()
    return await _get(session, user_id)


@router.post("/{user_id}/restore")
async def restore(user_id: int, admin: Moderator, request: Request, session: DbSession) -> StudentOut:
    profile = await session.get(Profile, user_id)
    if profile is None:
        raise not_found("Student")
    profile.app_status = AppStatus.ACTIVE
    profile.suspended_reason = None
    audit.record(
        session,
        "student.restored",
        admin_id=admin.id,
        target_type="student",
        target_id=str(user_id),
        ip=client_ip(request),
    )
    await session.commit()
    return await _get(session, user_id)


@router.post("/{user_id}/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_everywhere(user_id: int, admin: Moderator, request: Request, session: DbSession) -> None:
    await _get(session, user_id)
    await auth.revoke_all(session, user_id)
    audit.record(
        session,
        "student.signed_out",
        admin_id=admin.id,
        target_type="student",
        target_id=str(user_id),
        ip=client_ip(request),
    )
    await session.commit()


@router.post("/{user_id}/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def send_password_reset(
    user_id: int, admin: Moderator, request: Request, session: DbSession
) -> dict[str, str]:
    user = await web_users.by_id(session, user_id)
    if user is None or await session.get(Profile, user_id) is None:
        raise not_found("Student")
    await auth.send_code(session, user, CodePurpose.RESET_PASSWORD)
    audit.record(
        session,
        "student.password_reset_sent",
        admin_id=admin.id,
        target_type="student",
        target_id=str(user_id),
        ip=client_ip(request),
    )
    await session.commit()
    return {"message": f"A reset code was emailed to {user.email}."}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_student(user_id: int, admin: SuperAdmin, request: Request, session: DbSession) -> None:
    """Erase a student's personal data (also removes their website login). Super admins only."""
    await _get(session, user_id)
    await erase_student(session, user_id, requested_by="admin")
    audit.record(
        session,
        "student.deleted",
        admin_id=admin.id,
        target_type="student",
        target_id=str(user_id),
        ip=client_ip(request),
    )
    await session.commit()
