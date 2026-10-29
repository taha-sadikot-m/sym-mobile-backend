"""First-time content: countries, the SYM catalogue, debate topics and default committee seats.

Safe to run again: it only adds what is missing and never overwrites an admin's edits.
"""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.importer import ImportResult, import_catalog, parse_catalog_json
from app.content.seed.countries import GROUPINGS, UNSC_2026, all_countries
from app.models import Committee, CommitteeSeat, ContentStatus, Country, DebateTopic, SeatRule

SEED_DIR = Path(__file__).parent / "seed"

# Committees whose real membership is a known, limited set of countries.
DEFAULT_SEATS: dict[str, set[str]] = {
    "unsc": UNSC_2026,
    "nato": GROUPINGS["NATO"],
    "arab-league": GROUPINGS["Arab League"],
    "african-union": GROUPINGS["AU"],
}


@dataclass
class SeedResult:
    countries_added: int
    catalog: ImportResult
    topics_added: int
    committees_seated: int


async def seed_countries(session: AsyncSession) -> int:
    existing = set(await session.scalars(select(Country.code)))
    added = 0
    for row in all_countries():
        code = str(row["code"]).lower()
        if code in existing:
            continue
        session.add(Country(**{**row, "code": code}))
        added += 1
    await session.flush()
    return added


async def seed_topics(session: AsyncSession) -> int:
    topics = json.loads((SEED_DIR / "debate_topics.json").read_text(encoding="utf-8"))
    existing = set(await session.scalars(select(DebateTopic.slug)))
    added = 0
    for order, topic in enumerate(topics):
        if topic["slug"] in existing:
            continue
        session.add(DebateTopic(**topic, sort_order=order, status=ContentStatus.PUBLISHED))
        added += 1
    await session.flush()
    return added


async def seed_default_seats(session: AsyncSession, committee_ids: list[uuid.UUID]) -> int:
    """Only for committees created in this run, so an admin's seat choices are never replaced."""
    seated = 0
    for committee in await session.scalars(select(Committee).where(Committee.id.in_(committee_ids))):
        codes = DEFAULT_SEATS.get(committee.slug)
        if not codes:
            continue
        committee.seat_rule = SeatRule.SELECTED
        session.add_all(
            CommitteeSeat(committee_id=committee.id, country_code=code.lower()) for code in sorted(codes)
        )
        seated += 1
    await session.flush()
    return seated


async def seed_all(session: AsyncSession) -> SeedResult:
    countries_added = await seed_countries(session)
    catalog = await import_catalog(
        session, parse_catalog_json((SEED_DIR / "sym_mun_catalog.json").read_bytes())
    )
    topics_added = await seed_topics(session)
    seated = await seed_default_seats(session, catalog.new_committee_ids)
    return SeedResult(countries_added, catalog, topics_added, seated)
