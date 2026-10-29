from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import func, select

from app.content.seeding import SEED_DIR
from app.core.db import SessionLocal
from app.models import Agenda, AuditLog

AdminClient = Callable[[str], Awaitable[httpx.AsyncClient]]

pytestmark = pytest.mark.usefixtures("seeded")


async def _committee(client: httpx.AsyncClient, slug: str) -> dict:  # type: ignore[type-arg]
    committees = (await client.get("/admin/api/committees")).json()
    return next(item for item in committees if item["slug"] == slug)


async def test_every_role_can_read_but_only_editors_can_change(admin_client: AdminClient) -> None:
    analyst = await admin_client("analyst")
    assert len((await analyst.get("/admin/api/committees")).json()) == 30
    unsc = await _committee(analyst, "unsc")
    denied = await analyst.patch(f"/admin/api/committees/{unsc['id']}", json={"name": "x" * 10})
    assert denied.status_code == 403

    editor = await admin_client("content_manager")
    allowed = await editor.patch(f"/admin/api/committees/{unsc['id']}", json={"difficulty": "Intermediate"})
    assert allowed.status_code == 200
    assert allowed.json()["difficulty"] == "Intermediate"


async def test_changes_are_audited_with_before_and_after(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    who = await _committee(editor, "who")
    await editor.patch(
        f"/admin/api/committees/{who['id']}", json={"difficulty": "Advanced", "name": who["name"]}
    )
    async with SessionLocal() as session:
        entry = await session.scalar(select(AuditLog).where(AuditLog.action == "committee.updated"))
    assert entry is not None
    assert entry.details["changes"] == {"difficulty": ["Beginner", "Advanced"]}


async def test_committee_with_agendas_cannot_be_deleted(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    unsc = await _committee(editor, "unsc")
    response = await editor.delete(f"/admin/api/committees/{unsc['id']}")
    assert response.status_code == 409
    assert "Archive" in response.json()["detail"]

    created = await editor.post("/admin/api/committees", json={"abbr": "BRICS", "name": "BRICS Summit"})
    assert created.status_code == 201
    assert created.json()["slug"] == "brics"
    assert (await editor.delete(f"/admin/api/committees/{created.json()['id']}")).status_code == 204


async def test_seats_are_validated(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    unhrc = await _committee(editor, "unhrc")
    url = f"/admin/api/committees/{unhrc['id']}/seats"
    unknown = await editor.put(url, json={"seat_rule": "selected", "country_codes": ["IN", "ZZ"]})
    assert unknown.status_code == 409 and "ZZ" in unknown.json()["detail"]
    too_few = await editor.put(url, json={"seat_rule": "selected", "country_codes": ["IN"]})
    assert too_few.status_code == 409
    ok = await editor.put(url, json={"seat_rule": "selected", "country_codes": ["IN", "br", "ZA"]})
    assert ok.json() == {"seat_rule": "selected", "country_codes": ["br", "in", "za"]}
    reopened = await editor.put(url, json={"seat_rule": "all_un_members", "country_codes": ["IN"]})
    assert reopened.json() == {"seat_rule": "all_un_members", "country_codes": []}


async def test_agenda_review_flow(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    page = (await editor.get("/admin/api/agendas", params={"status": "in_review", "limit": 200})).json()
    assert page["total"] == 48
    ids = [item["id"] for item in page["items"][:5]]
    bulk = await editor.post("/admin/api/agendas/bulk-status", json={"ids": ids, "status": "published"})
    assert bulk.json() == {"updated": 5}
    remaining = (await editor.get("/admin/api/agendas", params={"status": "in_review"})).json()["total"]
    assert remaining == 43

    published = page["items"][0]
    blocked = await editor.delete(f"/admin/api/agendas/{published['id']}")
    assert blocked.status_code == 409


async def test_agenda_search_and_create(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    found = (await editor.get("/admin/api/agendas", params={"q": "Haiti"})).json()
    assert found["total"] >= 1 and all("Haiti" in item["title"] for item in found["items"])

    unsc = await _committee(editor, "unsc")
    body = {"committee_id": unsc["id"], "title": "Securing undersea internet cables", "status": "draft"}
    first = await editor.post("/admin/api/agendas", json=body)
    second = await editor.post("/admin/api/agendas", json=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["slug"] != second.json()["slug"]
    assert (await editor.delete(f"/admin/api/agendas/{second.json()['id']}")).status_code == 204


async def test_countries_reject_duplicates(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    countries = (await editor.get("/admin/api/countries")).json()
    assert len(countries) == 195
    duplicate = await editor.post(
        "/admin/api/countries", json={"code": "IN", "name": "India", "region": "Asia-Pacific Group"}
    )
    assert duplicate.status_code == 409
    renamed = await editor.patch("/admin/api/countries/tr", json={"name": "india"})
    assert renamed.status_code == 409
    summary = await editor.patch(
        "/admin/api/countries/tr", json={"summary": "Bridge between Europe and Asia."}
    )
    assert summary.json()["summary"] == "Bridge between Europe and Asia."


async def test_topics_need_points_on_both_sides_to_publish(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    body = {"title": "Space exploration is worth the cost", "category": "technology", "status": "published"}
    rejected = await editor.post("/admin/api/topics", json=body)
    assert rejected.status_code == 409
    body |= {"for_points": ["drives innovation", "inspires students"], "against_points": ["costly", "risky"]}
    assert (await editor.post("/admin/api/topics", json=body)).status_code == 201


async def test_settings_are_validated_and_super_admin_only(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    blocked = await editor.put("/admin/api/settings/pois", json={"value": {"count": 4}})
    assert blocked.status_code == 403

    owner = await admin_client("super_admin")
    bad = await owner.put(
        "/admin/api/settings/speech", json={"value": {"target_seconds": 90, "hard_stop_seconds": 60}}
    )
    assert bad.status_code == 422
    assert "hard stop" in bad.json()["detail"]

    good = await owner.put("/admin/api/settings/pois", json={"value": {"count": 4}})
    assert good.status_code == 200
    assert good.json()["value"]["count"] == 4 and good.json()["is_default"] is False
    reset = await owner.post("/admin/api/settings/pois/reset")
    assert reset.json()["value"]["count"] == 3 and reset.json()["is_default"] is True


async def test_import_preview_changes_nothing(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    csv = (
        b"Committee,Agenda,Source\nUNSC,Regulating private military companies in conflict zones,From MyMUN\n"
    )
    async with SessionLocal() as session:
        before = await session.scalar(select(func.count()).select_from(Agenda))

    preview = await editor.post("/admin/api/imports", files={"file": ("agendas.csv", csv, "text/csv")})
    assert preview.json()["applied"] is False and preview.json()["agendas"]["created"] == 1
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Agenda)) == before

    applied = await editor.post(
        "/admin/api/imports", params={"apply": "true"}, files={"file": ("agendas.csv", csv, "text/csv")}
    )
    assert applied.json()["applied"] is True
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Agenda)) == (before or 0) + 1


async def test_import_rejects_unknown_formats(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    response = await editor.post("/admin/api/imports", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 422
    catalog = (SEED_DIR / "sym_mun_catalog.json").read_bytes()
    same = await editor.post(
        "/admin/api/imports", files={"file": ("catalog.json", catalog, "application/json")}
    )
    assert same.json()["committees"] == {"created": 0, "updated": 0, "unchanged": 30}


async def test_activity_log_filters_by_area(admin_client: AdminClient) -> None:
    editor = await admin_client("content_manager")
    unsc = await _committee(editor, "unsc")
    await editor.patch(f"/admin/api/committees/{unsc['id']}", json={"difficulty": "Intermediate"})
    page = (await editor.get("/admin/api/activity", params={"area": "committee"})).json()
    assert page["total"] == 1 and page["items"][0]["action"] == "committee.updated"
    everything = (await editor.get("/admin/api/activity")).json()
    assert everything["total"] >= 2  # includes the sign-in
