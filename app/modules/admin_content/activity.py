import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.models import AdminUser, AuditLog
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, require_admin_header
from app.modules.admin_content.common import PageOut

router = APIRouter(
    prefix="/admin/api/activity", tags=["admin activity"], dependencies=[Depends(require_admin_header)]
)


class ActivityOut(BaseModel):
    id: uuid.UUID
    action: str
    admin_email: str | None
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    ip: str | None
    created_at: datetime


@router.get("")
async def list_activity(
    _: CurrentAdmin,
    session: DbSession,
    area: Annotated[str | None, Query(max_length=32, description="e.g. admin, committee, agenda")] = None,
    admin_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageOut[ActivityOut]:
    stmt = select(AuditLog, AdminUser.email).outerjoin(AdminUser, AdminUser.id == AuditLog.admin_id)
    if area:
        stmt = stmt.where(AuditLog.action.startswith(f"{area}."))
    if admin_id:
        stmt = stmt.where(AuditLog.admin_id == admin_id)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = await session.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))
    items = [
        ActivityOut(
            id=entry.id,
            action=entry.action,
            admin_email=email,
            target_type=entry.target_type,
            target_id=entry.target_id,
            details=entry.details,
            ip=str(entry.ip) if entry.ip else None,
            created_at=entry.created_at,
        )
        for entry, email in rows
    ]
    return PageOut(items=items, total=total, limit=limit, offset=offset)
