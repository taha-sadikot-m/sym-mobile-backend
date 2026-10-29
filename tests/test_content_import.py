from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.content.importer import ImportFileError, import_file, parse_agendas_csv, parse_catalog_json
from app.content.seeding import SEED_DIR, seed_all
from app.core.db import SessionLocal
from app.models import Agenda, Committee, CommitteeSeat, ContentStatus, Country, SeatRule

CATALOG = (SEED_DIR / "sym_mun_catalog.json").read_bytes()
CSV_PATH = (
    Path(__file__).resolve().parents[2] / "mobile_application" / "support_document" / "sym_mun_agendas.csv"
)


def test_featured_and_library_agendas_are_merged() -> None:
    rows = parse_catalog_json(CATALOG)
    assert len(rows) == 30
    assert sum(len(row.agendas) for row in rows) == 275  # 291 entries, 16 listed twice
    unsc = next(row for row in rows if row.slug == "unsc")
    sahel = next(agenda for agenda in unsc.agendas if "Sahel" in agenda.title and agenda.is_featured)
    assert sahel.caucus_topics and sahel.key_frameworks


def test_unreadable_files_are_rejected_with_a_clear_message() -> None:
    with pytest.raises(ImportFileError, match="catalogue JSON"):
        parse_catalog_json(b"{not json")
    with pytest.raises(ImportFileError, match="Committee"):
        parse_agendas_csv(b"name,title\nx,y\n")


async def test_seed_sets_statuses_and_default_seats() -> None:
    async with SessionLocal() as session:
        result = await seed_all(session)
        await session.commit()
        assert result.countries_added == 195
        assert result.topics_added == 24

        drafts = set(
            await session.scalars(select(Committee.slug).where(Committee.status == ContentStatus.DRAFT))
        )
        assert drafts == {"crisis", "press", "icj", "icc", "aippm", "lok-sabha"}

        in_review = await session.scalar(select(func.count()).where(Agenda.status == ContentStatus.IN_REVIEW))
        assert in_review == 48

        unsc = await session.scalar(select(Committee).where(Committee.slug == "unsc"))
        assert unsc is not None and unsc.seat_rule == SeatRule.SELECTED
        seats = set(
            await session.scalars(
                select(CommitteeSeat.country_code).where(CommitteeSeat.committee_id == unsc.id)
            )
        )
        assert {"cn", "fr", "ru", "gb", "us"} <= seats and len(seats) == 15

        india = await session.get(Country, "in")
        assert india is not None and "BRICS" in india.groupings and india.summary


async def test_reimport_keeps_admin_decisions() -> None:
    async with SessionLocal() as session:
        await seed_all(session)
        unsc = await session.scalar(select(Committee).where(Committee.slug == "unsc"))
        assert unsc is not None
        unsc.status = ContentStatus.ARCHIVED
        unsc.sort_order = 99
        await session.commit()

        result = await import_file(session, "catalog.json", CATALOG)
        await session.commit()
        assert result.committees.created == 0 and result.agendas.created == 0
        await session.refresh(unsc)
        assert unsc.status == ContentStatus.ARCHIVED and unsc.sort_order == 99


@pytest.mark.skipif(not CSV_PATH.exists(), reason="co-founder CSV not checked out next to the backend")
async def test_csv_import_matches_the_json_catalogue() -> None:
    async with SessionLocal() as session:
        await seed_all(session)
        await session.commit()
        result = await import_file(session, "agendas.csv", CSV_PATH.read_bytes())
        assert (result.agendas.created, result.agendas.updated) == (0, 0)
        assert result.problems == []


async def test_csv_reports_unknown_committees() -> None:
    async with SessionLocal() as session:
        await seed_all(session)
        csv = (
            "Committee,Agenda,Source\n"
            "UNSC,A brand new agenda on space debris,From MyMUN\n"
            "XYZ,Something,From MyMUN\n"
        )
        result = await import_file(session, "agendas.csv", csv.encode())
        assert result.agendas.created == 1
        assert result.problems == ["No committee with abbreviation “XYZ” — 1 agenda(s) skipped."]
