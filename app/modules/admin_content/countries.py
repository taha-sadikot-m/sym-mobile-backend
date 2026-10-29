from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core import audit
from app.models import CommitteeSeat, Country
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import ContentEditor, apply_changes, conflict, not_found, pairs

router = APIRouter(
    prefix="/admin/api/countries", tags=["admin countries"], dependencies=[Depends(require_admin_header)]
)

Grouping = Annotated[str, Field(min_length=1, max_length=32)]


class CountryFields(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    region: str = Field(min_length=2, max_length=64)
    is_un_member: bool = True
    groupings: list[Grouping] = Field(default_factory=list, max_length=20)
    summary: str = Field("", max_length=500)
    is_active: bool = True


class CountryCreate(CountryFields):
    code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")


class CountryPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    region: str | None = Field(None, min_length=2, max_length=64)
    is_un_member: bool | None = None
    groupings: list[Grouping] | None = Field(None, max_length=20)
    summary: str | None = Field(None, max_length=500)
    is_active: bool | None = None


class CountryOut(CountryFields):
    code: str
    seat_count: int
    updated_at: datetime


async def _out(session: DbSession, countries: list[Country]) -> list[CountryOut]:
    seats = await pairs(
        session,
        select(CommitteeSeat.country_code, func.count())
        .where(CommitteeSeat.country_code.in_([country.code for country in countries]))
        .group_by(CommitteeSeat.country_code),
    )
    return [
        CountryOut.model_validate(
            {
                **{
                    name: getattr(country, name) for name in CountryOut.model_fields if hasattr(country, name)
                },
                "seat_count": seats.get(country.code, 0),
            }
        )
        for country in countries
    ]


async def _name_taken(session: DbSession, name: str, code: str | None = None) -> bool:
    stmt = select(Country.code).where(func.lower(Country.name) == name.strip().lower())
    if code:
        stmt = stmt.where(Country.code != code)
    return await session.scalar(stmt) is not None


@router.get("")
async def list_countries(_: CurrentAdmin, session: DbSession) -> list[CountryOut]:
    """All countries (about 200); the console filters them in the browser."""
    return await _out(session, list(await session.scalars(select(Country).order_by(Country.name))))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_country(
    body: CountryCreate, admin: ContentEditor, request: Request, session: DbSession
) -> CountryOut:
    code = body.code.lower()
    if await session.get(Country, code):
        raise conflict(f"A country with code {code.upper()} already exists.")
    if await _name_taken(session, body.name):
        raise conflict(f"“{body.name}” already exists.")
    country = Country(code=code, **body.model_dump(exclude={"code"}))
    session.add(country)
    audit.record(
        session,
        "country.created",
        admin_id=admin.id,
        target_type="country",
        target_id=code,
        ip=client_ip(request),
        name=country.name,
    )
    await session.commit()
    return (await _out(session, [country]))[0]


@router.patch("/{code}")
async def update_country(
    code: str, body: CountryPatch, admin: ContentEditor, request: Request, session: DbSession
) -> CountryOut:
    country = await session.get(Country, code.lower())
    if country is None:
        raise not_found("Country")
    if body.name and await _name_taken(session, body.name, country.code):
        raise conflict(f"“{body.name}” already exists.")
    changes = apply_changes(country, body.model_dump(exclude_unset=True))
    if changes:
        audit.record(
            session,
            "country.updated",
            admin_id=admin.id,
            target_type="country",
            target_id=country.code,
            ip=client_ip(request),
            name=country.name,
            changes=changes,
        )
        await session.commit()
    return (await _out(session, [country]))[0]
