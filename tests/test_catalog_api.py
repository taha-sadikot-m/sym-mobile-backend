from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import func, select, update

from app.core.db import SessionLocal
from app.main import app
from app.models import Agenda, Committee, ContentStatus
from app.modules.catalog.router import clear_cache

AdminClient = Callable[[str], Awaitable[httpx.AsyncClient]]

pytestmark = pytest.mark.usefixtures("seeded")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_catalog_has_only_published_content() -> None:
    async with _client() as client:
        catalog = (await client.get("/v1/catalog")).json()
    slugs = {committee["slug"] for committee in catalog["committees"]}
    assert len(slugs) == 24 and "crisis" not in slugs and "unsc" in slugs
    async with SessionLocal() as session:
        expected = await session.scalar(
            select(func.count())
            .select_from(Agenda)
            .join(Committee, Committee.id == Agenda.committee_id)
            .where(Agenda.status == ContentStatus.PUBLISHED, Committee.status == ContentStatus.PUBLISHED)
        )
    agendas = sum(len(committee["agendas"]) for committee in catalog["committees"])
    assert agendas == expected  # in-review agendas and agendas of draft committees are hidden
    assert 0 < agendas < 227
    assert len(catalog["countries"]) == 195
    assert len(catalog["topics"]) == 24
    unsc = next(committee for committee in catalog["committees"] if committee["slug"] == "unsc")
    assert unsc["seat_rule"] == "selected" and len(unsc["seats"]) == 15
    who = next(committee for committee in catalog["committees"] if committee["slug"] == "who")
    assert who["seat_rule"] == "all_un_members" and who["seats"] == []


async def test_committees_with_no_published_agendas_are_hidden() -> None:
    async with SessionLocal() as session:
        who = await session.scalar(select(Committee).where(Committee.slug == "who"))
        assert who is not None
        await session.execute(
            update(Agenda).where(Agenda.committee_id == who.id).values(status=ContentStatus.DRAFT)
        )
        await session.commit()
    async with _client() as client:
        catalog = (await client.get("/v1/catalog")).json()
    assert "who" not in {committee["slug"] for committee in catalog["committees"]}


async def test_unchanged_catalog_returns_304() -> None:
    async with _client() as client:
        first = await client.get("/v1/catalog")
        etag = first.headers["etag"]
        assert first.json()["version"] == etag.strip('"')
        again = await client.get("/v1/catalog", headers={"If-None-Match": etag})
    assert again.status_code == 304


async def test_config_reflects_admin_changes(admin_client: AdminClient) -> None:
    async with _client() as client:
        before = (await client.get("/v1/config")).json()
    assert before["pois"]["count"] == 3
    assert {item["id"] for item in before["live_formats"]["formats"]} == {"quick", "standard", "rapid"}

    owner = await admin_client("super_admin")
    await owner.put("/admin/api/settings/pois", json={"value": {"count": 2, "answer_seconds": 60}})
    clear_cache()
    async with _client() as client:
        after = (await client.get("/v1/config")).json()
    assert (after["pois"]["count"], after["pois"]["answer_seconds"]) == (2, 60)
    assert after["version"] != before["version"]
