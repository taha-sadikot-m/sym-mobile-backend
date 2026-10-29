import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.content.importer import slugify
from app.core import audit
from app.models import (
    Agenda,
    Committee,
    CommitteeSeat,
    ContentStatus,
    Country,
    Difficulty,
    SeatRule,
    SeatType,
)
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import ContentEditor, apply_changes, conflict, not_found, pairs

router = APIRouter(
    prefix="/admin/api/committees", tags=["admin committees"], dependencies=[Depends(require_admin_header)]
)


class Theme(BaseModel):
    theme: str = Field(min_length=1, max_length=120)
    share_pct: int | None = Field(None, ge=0, le=100)


class CommitteeFields(BaseModel):
    abbr: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=2, max_length=160)
    category: str = Field("", max_length=64)
    seat_type: SeatType = SeatType.COUNTRY
    difficulty: Difficulty = Difficulty.BEGINNER
    description: str = Field("", max_length=2000)
    decision_rule: str = Field("", max_length=1000)
    binding: str = Field("", max_length=500)
    typical_seats: int | None = Field(None, ge=1, le=500)
    themes: list[Theme] = Field(default_factory=list, max_length=20)
    status: ContentStatus = ContentStatus.DRAFT


class CommitteeCreate(CommitteeFields):
    slug: str | None = Field(None, max_length=64, pattern=r"^[a-z0-9-]+$")


class CommitteePatch(BaseModel):
    abbr: str | None = Field(None, min_length=1, max_length=32)
    name: str | None = Field(None, min_length=2, max_length=160)
    category: str | None = Field(None, max_length=64)
    seat_type: SeatType | None = None
    difficulty: Difficulty | None = None
    description: str | None = Field(None, max_length=2000)
    decision_rule: str | None = Field(None, max_length=1000)
    binding: str | None = Field(None, max_length=500)
    typical_seats: int | None = Field(None, ge=1, le=500)
    themes: list[Theme] | None = Field(None, max_length=20)
    status: ContentStatus | None = None


class CommitteeOut(CommitteeFields):
    id: uuid.UUID
    slug: str
    seat_rule: SeatRule
    sort_order: int
    agenda_count: int
    published_agenda_count: int
    seat_count: int
    updated_at: datetime


class SeatsIn(BaseModel):
    seat_rule: SeatRule
    country_codes: list[str] = Field(default_factory=list, max_length=250)


class SeatsOut(BaseModel):
    seat_rule: SeatRule
    country_codes: list[str]


class ReorderIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


async def _out(session: DbSession, committees: list[Committee]) -> list[CommitteeOut]:
    ids = [committee.id for committee in committees]
    agenda_counts = await pairs(
        session,
        select(Agenda.committee_id, func.count())
        .where(Agenda.committee_id.in_(ids))
        .group_by(Agenda.committee_id),
    )
    published_counts = await pairs(
        session,
        select(Agenda.committee_id, func.count())
        .where(Agenda.committee_id.in_(ids), Agenda.status == ContentStatus.PUBLISHED)
        .group_by(Agenda.committee_id),
    )
    seat_counts = await pairs(
        session,
        select(CommitteeSeat.committee_id, func.count())
        .where(CommitteeSeat.committee_id.in_(ids))
        .group_by(CommitteeSeat.committee_id),
    )
    return [
        CommitteeOut.model_validate(
            {
                **{
                    column: getattr(committee, column)
                    for column in CommitteeOut.model_fields
                    if hasattr(committee, column)
                },
                "agenda_count": agenda_counts.get(committee.id, 0),
                "published_agenda_count": published_counts.get(committee.id, 0),
                "seat_count": seat_counts.get(committee.id, 0),
            }
        )
        for committee in committees
    ]


async def _get(session: DbSession, committee_id: uuid.UUID) -> Committee:
    committee = await session.get(Committee, committee_id)
    if committee is None:
        raise not_found("Committee")
    return committee


@router.get("")
async def list_committees(_: CurrentAdmin, session: DbSession) -> list[CommitteeOut]:
    committees = list(await session.scalars(select(Committee).order_by(Committee.sort_order, Committee.abbr)))
    return await _out(session, committees)


@router.get("/{committee_id}")
async def get_committee(committee_id: uuid.UUID, _: CurrentAdmin, session: DbSession) -> CommitteeOut:
    return (await _out(session, [await _get(session, committee_id)]))[0]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_committee(
    body: CommitteeCreate, admin: ContentEditor, request: Request, session: DbSession
) -> CommitteeOut:
    slug = body.slug or slugify(body.abbr, 64)
    if not slug:
        raise conflict("Give the committee an abbreviation with letters or numbers.")
    if await session.scalar(select(Committee).where(Committee.slug == slug)):
        raise conflict(f"A committee with the short name “{slug}” already exists.")
    last = await session.scalar(select(func.max(Committee.sort_order))) or 0
    values: dict[str, Any] = body.model_dump(exclude={"slug"})
    committee = Committee(slug=slug, sort_order=last + 1, **values)
    session.add(committee)
    await session.flush()
    audit.record(
        session,
        "committee.created",
        admin_id=admin.id,
        target_type="committee",
        target_id=str(committee.id),
        ip=client_ip(request),
        abbr=committee.abbr,
    )
    await session.commit()
    return (await _out(session, [committee]))[0]


@router.patch("/{committee_id}")
async def update_committee(
    committee_id: uuid.UUID, body: CommitteePatch, admin: ContentEditor, request: Request, session: DbSession
) -> CommitteeOut:
    committee = await _get(session, committee_id)
    changes = apply_changes(committee, body.model_dump(exclude_unset=True, mode="json"))
    if changes:
        audit.record(
            session,
            "committee.updated",
            admin_id=admin.id,
            target_type="committee",
            target_id=str(committee.id),
            ip=client_ip(request),
            abbr=committee.abbr,
            changes=changes,
        )
        await session.commit()
    return (await _out(session, [committee]))[0]


@router.delete("/{committee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_committee(
    committee_id: uuid.UUID, admin: ContentEditor, request: Request, session: DbSession
) -> None:
    committee = await _get(session, committee_id)
    agendas = await session.scalar(select(func.count()).where(Agenda.committee_id == committee.id)) or 0
    if agendas:
        raise conflict(f"{committee.abbr} still has {agendas} agenda(s). Archive the committee instead.")
    await session.delete(committee)
    audit.record(
        session,
        "committee.deleted",
        admin_id=admin.id,
        target_type="committee",
        target_id=str(committee_id),
        ip=client_ip(request),
        abbr=committee.abbr,
    )
    await session.commit()


@router.post("/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_committees(
    body: ReorderIn, admin: ContentEditor, request: Request, session: DbSession
) -> None:
    committees = {committee.id: committee for committee in await session.scalars(select(Committee))}
    if set(body.ids) != set(committees):
        raise conflict("The list changed while you were reordering. Reload and try again.")
    for order, committee_id in enumerate(body.ids):
        committees[committee_id].sort_order = order
    audit.record(
        session, "committee.reordered", admin_id=admin.id, target_type="committee", ip=client_ip(request)
    )
    await session.commit()


@router.get("/{committee_id}/seats")
async def get_seats(committee_id: uuid.UUID, _: CurrentAdmin, session: DbSession) -> SeatsOut:
    committee = await _get(session, committee_id)
    codes = await session.scalars(
        select(CommitteeSeat.country_code)
        .where(CommitteeSeat.committee_id == committee.id)
        .order_by(CommitteeSeat.country_code)
    )
    return SeatsOut(seat_rule=committee.seat_rule, country_codes=list(codes))


@router.put("/{committee_id}/seats")
async def set_seats(
    committee_id: uuid.UUID, body: SeatsIn, admin: ContentEditor, request: Request, session: DbSession
) -> SeatsOut:
    committee = await _get(session, committee_id)
    codes = sorted({code.strip().lower() for code in body.country_codes if code.strip()})
    if body.seat_rule == SeatRule.SELECTED:
        if len(codes) < 2:
            raise conflict("Seat at least two countries, or open the committee to all UN members.")
        known = set(await session.scalars(select(Country.code).where(Country.code.in_(codes))))
        unknown = [code.upper() for code in codes if code not in known]
        if unknown:
            raise conflict(f"Unknown country code(s): {', '.join(unknown)}.")
    else:
        codes = []
    before = sorted(
        await session.scalars(
            select(CommitteeSeat.country_code).where(CommitteeSeat.committee_id == committee.id)
        )
    )
    previous_rule = committee.seat_rule
    await session.execute(delete(CommitteeSeat).where(CommitteeSeat.committee_id == committee.id))
    session.add_all(CommitteeSeat(committee_id=committee.id, country_code=code) for code in codes)
    committee.seat_rule = body.seat_rule
    audit.record(
        session,
        "committee.seats_updated",
        admin_id=admin.id,
        target_type="committee",
        target_id=str(committee.id),
        ip=client_ip(request),
        abbr=committee.abbr,
        seat_rule=[previous_rule, body.seat_rule],
        added=sorted(set(codes) - set(before)),
        removed=sorted(set(before) - set(codes)),
    )
    await session.commit()
    return SeatsOut(seat_rule=committee.seat_rule, country_codes=codes)
