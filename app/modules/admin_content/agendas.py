import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.content.importer import slugify
from app.core import audit
from app.models import Agenda, Committee, ContentStatus
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import (
    ContentEditor,
    PageOut,
    apply_changes,
    conflict,
    not_found,
    paginate,
    pairs,
)

router = APIRouter(
    prefix="/admin/api/agendas", tags=["admin agendas"], dependencies=[Depends(require_admin_header)]
)

ShortList = Annotated[list[Annotated[str, Field(min_length=1, max_length=300)]], Field(max_length=12)]


class AgendaFields(BaseModel):
    title: str = Field(min_length=5, max_length=300)
    theme: str | None = Field(None, max_length=120)
    seen_at: str | None = Field(None, max_length=200)
    is_featured: bool = False
    caucus_topics: ShortList = Field(default_factory=list)
    key_frameworks: ShortList = Field(default_factory=list)
    status: ContentStatus = ContentStatus.DRAFT


class AgendaCreate(AgendaFields):
    committee_id: uuid.UUID


class AgendaPatch(BaseModel):
    title: str | None = Field(None, min_length=5, max_length=300)
    theme: str | None = Field(None, max_length=120)
    seen_at: str | None = Field(None, max_length=200)
    is_featured: bool | None = None
    caucus_topics: ShortList | None = None
    key_frameworks: ShortList | None = None
    status: ContentStatus | None = None
    committee_id: uuid.UUID | None = None


class AgendaOut(AgendaFields):
    id: uuid.UUID
    committee_id: uuid.UUID
    committee_abbr: str
    slug: str
    kind: str
    sort_order: int
    updated_at: datetime


class BulkStatusIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    status: ContentStatus


class BulkStatusOut(BaseModel):
    updated: int


def _out(agenda: Agenda, abbr: str) -> AgendaOut:
    return AgendaOut.model_validate(
        {
            **{name: getattr(agenda, name) for name in AgendaOut.model_fields if hasattr(agenda, name)},
            "committee_abbr": abbr,
        }
    )


async def _abbr(session: DbSession, committee_id: uuid.UUID) -> str:
    abbr = await session.scalar(select(Committee.abbr).where(Committee.id == committee_id))
    if abbr is None:
        raise not_found("Committee")
    return abbr


async def _get(session: DbSession, agenda_id: uuid.UUID) -> Agenda:
    agenda = await session.get(Agenda, agenda_id)
    if agenda is None:
        raise not_found("Agenda")
    return agenda


async def _unique_slug(
    session: DbSession, committee_id: uuid.UUID, title: str, *, exclude: uuid.UUID | None = None
) -> str:
    base = slugify(title) or "agenda"
    slug, suffix = base, 2
    while await session.scalar(
        select(Agenda.id).where(
            Agenda.committee_id == committee_id, Agenda.slug == slug, Agenda.id != exclude
        )
    ):
        slug = f"{base[:56]}-{suffix}"
        suffix += 1
    return slug


@router.get("")
async def list_agendas(
    _: CurrentAdmin,
    session: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    committee_id: uuid.UUID | None = None,
    status_filter: Annotated[ContentStatus | None, Query(alias="status")] = None,
    featured: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageOut[AgendaOut]:
    stmt = select(Agenda).join(Committee, Committee.id == Agenda.committee_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Agenda.title.ilike(like), Agenda.theme.ilike(like), Committee.abbr.ilike(like)))
    if committee_id:
        stmt = stmt.where(Agenda.committee_id == committee_id)
    if status_filter:
        stmt = stmt.where(Agenda.status == status_filter)
    if featured is not None:
        stmt = stmt.where(Agenda.is_featured.is_(featured))
    stmt = stmt.order_by(Committee.sort_order, Agenda.sort_order, Agenda.title)
    rows, total = await paginate(session, stmt, limit=limit, offset=offset)
    abbrs = await pairs(
        session,
        select(Committee.id, Committee.abbr).where(Committee.id.in_({row.committee_id for row in rows})),
    )
    return PageOut(
        items=[_out(row, abbrs[row.committee_id]) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{agenda_id}")
async def get_agenda(agenda_id: uuid.UUID, _: CurrentAdmin, session: DbSession) -> AgendaOut:
    agenda = await _get(session, agenda_id)
    return _out(agenda, await _abbr(session, agenda.committee_id))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agenda(
    body: AgendaCreate, admin: ContentEditor, request: Request, session: DbSession
) -> AgendaOut:
    abbr = await _abbr(session, body.committee_id)
    last = await session.scalar(
        select(func.max(Agenda.sort_order)).where(Agenda.committee_id == body.committee_id)
    )
    agenda = Agenda(
        **body.model_dump(),
        slug=await _unique_slug(session, body.committee_id, body.title),
        kind="admin",
        sort_order=(last or 0) + 1,
    )
    session.add(agenda)
    await session.flush()
    audit.record(
        session,
        "agenda.created",
        admin_id=admin.id,
        target_type="agenda",
        target_id=str(agenda.id),
        ip=client_ip(request),
        committee=abbr,
        title=agenda.title,
    )
    await session.commit()
    return _out(agenda, abbr)


@router.patch("/{agenda_id}")
async def update_agenda(
    agenda_id: uuid.UUID, body: AgendaPatch, admin: ContentEditor, request: Request, session: DbSession
) -> AgendaOut:
    agenda = await _get(session, agenda_id)
    values = body.model_dump(exclude_unset=True, mode="json")
    if "committee_id" in values:
        values["committee_id"] = uuid.UUID(values["committee_id"])
        await _abbr(session, values["committee_id"])
        if values["committee_id"] != agenda.committee_id:
            values["slug"] = await _unique_slug(
                session, values["committee_id"], values.get("title", agenda.title)
            )
    changes = apply_changes(agenda, values)
    abbr = await _abbr(session, agenda.committee_id)
    if changes:
        audit.record(
            session,
            "agenda.updated",
            admin_id=admin.id,
            target_type="agenda",
            target_id=str(agenda.id),
            ip=client_ip(request),
            committee=abbr,
            title=agenda.title,
            changes=changes,
        )
        await session.commit()
    return _out(agenda, abbr)


@router.delete("/{agenda_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agenda(
    agenda_id: uuid.UUID, admin: ContentEditor, request: Request, session: DbSession
) -> None:
    agenda = await _get(session, agenda_id)
    if agenda.status == ContentStatus.PUBLISHED:
        raise conflict("Published agendas can’t be deleted. Archive it first so students stop seeing it.")
    await session.delete(agenda)
    audit.record(
        session,
        "agenda.deleted",
        admin_id=admin.id,
        target_type="agenda",
        target_id=str(agenda_id),
        ip=client_ip(request),
        title=agenda.title,
    )
    await session.commit()


@router.post("/bulk-status")
async def bulk_status(
    body: BulkStatusIn, admin: ContentEditor, request: Request, session: DbSession
) -> BulkStatusOut:
    agendas = list(
        await session.scalars(select(Agenda).where(Agenda.id.in_(body.ids), Agenda.status != body.status))
    )
    for agenda in agendas:
        agenda.status = body.status
    if agendas:
        audit.record(
            session,
            "agenda.bulk_status",
            admin_id=admin.id,
            target_type="agenda",
            ip=client_ip(request),
            status=body.status,
            count=len(agendas),
            ids=[str(agenda.id) for agenda in agendas],
        )
        await session.commit()
    return BulkStatusOut(updated=len(agendas))
