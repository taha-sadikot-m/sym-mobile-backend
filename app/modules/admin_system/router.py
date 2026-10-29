import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import engine
from app.models import AdminUser, Agenda, AuditLog, Committee, ContentStatus, Country, DebateTopic
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, require_admin_header
from app.web.contract import check_contract

router = APIRouter(
    prefix="/admin/api/system", tags=["admin system"], dependencies=[Depends(require_admin_header)]
)


class AuditEntry(BaseModel):
    id: uuid.UUID
    action: str
    admin_email: str | None
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    ip: str | None
    created_at: datetime


class ContentCounts(BaseModel):
    committees_published: int
    committees_total: int
    agendas_published: int
    agendas_in_review: int
    agendas_total: int
    countries_active: int
    topics_published: int


class SystemStatus(BaseModel):
    version: str
    environment: str
    web_tables_ok: bool
    web_table_changes: list[str]
    active_admins: int
    content: ContentCounts
    recent_activity: list[AuditEntry]


@router.get("/status")
async def status(_: CurrentAdmin, session: DbSession) -> SystemStatus:
    settings = get_settings()
    async with engine.connect() as conn:
        drift = await check_contract(conn)
    active_admins = await session.scalar(
        select(func.count()).select_from(AdminUser).where(AdminUser.is_active)
    )

    async def count(model: type, *conditions: object) -> int:
        return await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0  # type: ignore[arg-type]

    content = ContentCounts(
        committees_published=await count(Committee, Committee.status == ContentStatus.PUBLISHED),
        committees_total=await count(Committee),
        agendas_published=await count(Agenda, Agenda.status == ContentStatus.PUBLISHED),
        agendas_in_review=await count(Agenda, Agenda.status == ContentStatus.IN_REVIEW),
        agendas_total=await count(Agenda),
        countries_active=await count(Country, Country.is_active),
        topics_published=await count(DebateTopic, DebateTopic.status == ContentStatus.PUBLISHED),
    )
    rows = await session.execute(
        select(AuditLog, AdminUser.email)
        .outerjoin(AdminUser, AdminUser.id == AuditLog.admin_id)
        .order_by(AuditLog.created_at.desc())
        .limit(25)
    )
    return SystemStatus(
        version=settings.version,
        environment=settings.environment,
        web_tables_ok=not drift,
        web_table_changes=[str(item) for item in drift],
        active_admins=active_admins or 0,
        content=content,
        recent_activity=[
            AuditEntry(
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
        ],
    )
