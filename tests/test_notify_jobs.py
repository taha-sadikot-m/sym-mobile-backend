"""Push tokens, live-debate notifications, reminders, the clean-up job and the admin AI tools."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from app import jobs
from app.ai import instructions
from app.core.db import SessionLocal
from app.main import app
from app.models import EmailCode, JobRun, Notification, PushToken
from app.models.base import utcnow
from app.models.live import Challenge
from app.notify import push
from tests.test_live_api import Pair, challenge, make_pair
from tests.test_student_auth import AdminClient

TOKEN = "ExponentPushToken[abcdefghijklmnop12345]"


@pytest.fixture
async def pair() -> AsyncIterator[Pair]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield await make_pair(client)


async def notifications(user_id: int) -> list[str]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Notification.kind).where(Notification.user_id == user_id).order_by(Notification.created_at)
        )
        return list(rows)


async def test_devices_register_and_move_between_accounts(pair: Pair) -> None:
    bad = await pair.client.put(
        "/v1/me/push-token", headers=pair.a, json={"token": "nope", "platform": "ios"}
    )
    assert bad.status_code == 422
    for headers in (pair.a, pair.b):  # the same phone signs in to Kabir's account next
        saved = await pair.client.put(
            "/v1/me/push-token", headers=headers, json={"token": TOKEN, "platform": "android"}
        )
        assert saved.status_code == 204
    async with SessionLocal() as session:
        row = await session.get(PushToken, TOKEN)
        assert row is not None and row.user_id == pair.b_id
    forgotten = await pair.client.request(
        "DELETE", "/v1/me/push-token", headers=pair.b, json={"token": TOKEN}
    )
    assert forgotten.status_code == 204
    async with SessionLocal() as session:
        assert await session.get(PushToken, TOKEN) is None


async def test_challenge_events_notify_the_other_student(pair: Pair) -> None:
    made = await challenge(pair)
    await pair.client.post(f"/v1/challenges/{made['id']}/accept", headers=pair.b)
    await pair.client.post(f"/v1/challenges/{made['id']}/ready", headers=pair.a)
    assert await notifications(pair.b_id) == ["challenge.received", "challenge.lobby"]
    assert await notifications(pair.a_id) == ["challenge.accepted"]


async def test_reminders_go_out_once_before_the_start(pair: Pair) -> None:
    made = await challenge(pair)
    await pair.client.post(f"/v1/challenges/{made['id']}/accept", headers=pair.b)
    async with SessionLocal() as session:
        await session.execute(
            update(Challenge)
            .where(Challenge.id == uuid.UUID(made["id"]))
            .values(scheduled_at=utcnow() + timedelta(minutes=10))
        )
        await session.commit()
    first = await jobs.send_reminders()
    again = await jobs.send_reminders()
    assert (first, again) == ({"reminded": 1}, {"reminded": 0})
    assert "challenge.reminder" in await notifications(pair.a_id)
    assert "challenge.reminder" in await notifications(pair.b_id)


async def test_cleanup_removes_expired_data_and_is_recorded(pair: Pair) -> None:
    async with SessionLocal() as session:
        session.add(
            EmailCode(
                user_id=pair.a_id,
                purpose="verify_email",
                code_hash="x" * 64,
                expires_at=utcnow() - timedelta(days=3),
            )
        )
        await session.commit()
    runs = await jobs.run_due(force_cleanup=True)
    assert [run.name for run in runs] == ["reminders", "cleanup"] and all(run.ok for run in runs)
    assert runs[1].details["email_codes"] >= 1
    async with SessionLocal() as session:
        assert (await session.scalar(select(JobRun).where(JobRun.name == "cleanup"))) is not None


async def test_push_sends_to_expo_and_forgets_dead_devices(
    pair: Pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    dead = "ExponentPushToken[deaddeaddeaddead00000]"
    for token in (TOKEN, dead):
        await pair.client.put("/v1/me/push-token", headers=pair.a, json={"token": token, "platform": "ios"})
    sent: list[dict[str, Any]] = []

    async def fake_send(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sent.extend(messages)
        return [
            {"status": "ok"}
            if m["to"] == TOKEN
            else {"status": "error", "details": {"error": "DeviceNotRegistered"}}
            for m in messages
        ]

    monkeypatch.setattr(push, "_send", fake_send)
    monkeypatch.setattr(push.get_settings(), "push_enabled", True)
    await push.notify([pair.a_id], kind="announcement", title="Hello", body="World")
    assert sorted(m["to"] for m in sent) == sorted([TOKEN, dead])
    async with SessionLocal() as session:
        assert await session.get(PushToken, dead) is None
        row = await session.scalar(select(Notification).where(Notification.user_id == pair.a_id))
        assert row is not None and (row.devices, row.delivered) == (2, 1)


async def test_admin_ai_guidance_reaches_every_prompt(admin_client: AdminClient) -> None:
    analyst = await admin_client("analyst")
    assert (await analyst.put("/admin/api/ai/instructions", json={"enabled": False})).status_code == 403
    editor = await admin_client("content_manager")
    saved = await editor.put(
        "/admin/api/ai/instructions",
        json={
            "enabled": True,
            "house_style": "Use Indian English.",
            "features": {"live.judge": "Be strict on time."},
        },
    )
    assert saved.status_code == 200
    guide = await instructions.current()
    assert "Use Indian English." in guide.guidance_for("live.judge")
    assert "Be strict on time." in guide.guidance_for("live.judge")
    assert "Be strict on time." not in guide.guidance_for("mun.feedback")
    unknown = await editor.put("/admin/api/ai/instructions", json={"features": {"made.up": "x"}})
    assert unknown.status_code == 422
    calls = (await analyst.get("/admin/api/ai/calls")).json()
    assert calls["total"] == 0 and calls["week"] == []
