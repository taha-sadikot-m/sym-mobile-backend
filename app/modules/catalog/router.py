"""What the app reads: published content (/v1/catalog) and rules and settings (/v1/config).

Both responses carry an ETag, so the app re-downloads only when something changed and can keep the last
copy for offline use.
"""

import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.settings import SETTINGS
from app.core.db import SessionLocal
from app.models import (
    Agenda,
    AppSetting,
    Committee,
    CommitteeSeat,
    ContentStatus,
    Country,
    DebateTopic,
    SeatRule,
)

router = APIRouter(prefix="/v1", tags=["app catalog"])

# Admin edits reach the app within this many seconds; repeat requests in between cost no database work.
CACHE_SECONDS = 30
_cache: dict[str, tuple[float, bytes, str]] = {}


async def build_catalog(session: AsyncSession) -> dict[str, Any]:
    committees = list(
        await session.scalars(
            select(Committee)
            .where(Committee.status == ContentStatus.PUBLISHED)
            .order_by(Committee.sort_order)
        )
    )
    ids = [committee.id for committee in committees]
    agendas: dict[Any, list[dict[str, Any]]] = {committee_id: [] for committee_id in ids}
    for agenda in await session.scalars(
        select(Agenda)
        .where(Agenda.committee_id.in_(ids), Agenda.status == ContentStatus.PUBLISHED)
        .order_by(Agenda.is_featured.desc(), Agenda.sort_order)
    ):
        agendas[agenda.committee_id].append(
            {
                "id": str(agenda.id),
                "title": agenda.title,
                "theme": agenda.theme,
                "featured": agenda.is_featured,
                "caucus_topics": agenda.caucus_topics,
                "key_frameworks": agenda.key_frameworks,
            }
        )
    seats: dict[Any, list[str]] = {committee_id: [] for committee_id in ids}
    for committee_id, code in await session.execute(
        select(CommitteeSeat.committee_id, CommitteeSeat.country_code)
        .where(CommitteeSeat.committee_id.in_(ids))
        .order_by(CommitteeSeat.country_code)
    ):
        seats[committee_id].append(code)
    countries = await session.scalars(select(Country).where(Country.is_active).order_by(Country.name))
    topics = await session.scalars(
        select(DebateTopic)
        .where(DebateTopic.status == ContentStatus.PUBLISHED)
        .order_by(DebateTopic.sort_order)
    )
    return {
        "committees": [
            {
                "id": str(committee.id),
                "slug": committee.slug,
                "abbr": committee.abbr,
                "name": committee.name,
                "category": committee.category,
                "seat_type": committee.seat_type,
                "difficulty": committee.difficulty,
                "description": committee.description,
                "decision_rule": committee.decision_rule,
                "binding": committee.binding,
                # "all_un_members": any active UN member may be seated; "selected": only `seats`.
                "seat_rule": committee.seat_rule,
                "seats": seats[committee.id] if committee.seat_rule == SeatRule.SELECTED else [],
                "agendas": agendas[committee.id],
            }
            for committee in committees
            if agendas[committee.id]
        ],
        "countries": [
            {
                "code": country.code,
                "name": country.name,
                "region": country.region,
                "is_un_member": country.is_un_member,
                "groupings": country.groupings,
                "summary": country.summary,
            }
            for country in countries
        ],
        "topics": [
            {
                "id": str(topic.id),
                "slug": topic.slug,
                "title": topic.title,
                "category": topic.category,
                "context": topic.context,
                "for_points": topic.for_points,
                "against_points": topic.against_points,
            }
            for topic in topics
        ],
    }


async def build_config(session: AsyncSession) -> dict[str, Any]:
    stored = {row.key: row.value for row in await session.scalars(select(AppSetting))}
    return {
        key: model.model_validate(stored.get(key, {})).model_dump(mode="json")
        for key, model in SETTINGS.items()
    }


def _encode(payload: dict[str, Any]) -> tuple[bytes, str]:
    """The body, with a `version` that changes whenever the content does (also used as the ETag)."""
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    version = hashlib.sha256(content.encode()).hexdigest()[:32]
    body = json.dumps({"version": version, **payload}, ensure_ascii=False, separators=(",", ":")).encode()
    return body, f'"{version}"'


async def _cached(name: str, builder: Any) -> tuple[bytes, str]:
    hit = _cache.get(name)
    if hit and hit[0] > time.monotonic():
        return hit[1], hit[2]
    async with SessionLocal() as session:
        body, etag = _encode(await builder(session))
    _cache[name] = (time.monotonic() + CACHE_SECONDS, body, etag)
    return body, etag


def _respond(request: Request, body: bytes, etag: str) -> Response:
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


def clear_cache() -> None:
    _cache.clear()


@router.get("/catalog")
async def catalog(request: Request) -> Response:
    """Published committees (with agendas and seats), active countries and published debate topics."""
    return _respond(request, *await _cached("catalog", build_catalog))


@router.get("/config")
async def config(request: Request) -> Response:
    """Rules and settings: speech and POI timings, live debate formats and rules, features, versions."""
    return _respond(request, *await _cached("config", build_config))
