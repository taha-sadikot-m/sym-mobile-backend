import time
from collections.abc import AsyncIterator

import httpx
import pyotp
import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.passwords import hash_admin_password
from app.main import app
from app.models import AdminRole, AdminUser, AuditLog

HEADERS = {"X-SYM-Admin": "1"}
EMAIL = "chair@symedu.in"
PASSWORD = "a-very-long-admin-password"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=HEADERS) as http:
        yield http


@pytest.fixture
async def admin() -> AdminUser:
    async with SessionLocal() as session:
        user = AdminUser(
            email=EMAIL,
            full_name="Head Chair",
            role=AdminRole.SUPER_ADMIN,
            password_hash=hash_admin_password(PASSWORD),
        )
        session.add(user)
        await session.commit()
        return user


def code_for(secret: str, *, step_offset: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + step_offset * 30)


async def sign_in(client: httpx.AsyncClient) -> str:
    first = await client.post("/admin/api/auth/password", json={"email": EMAIL, "password": PASSWORD})
    assert first.status_code == 200
    body = first.json()
    assert body["next"] == "totp_setup"
    assert body["qr_svg"].startswith("data:image/svg+xml")
    done = await client.post(
        "/admin/api/auth/code", json={"challenge": body["challenge"], "code": code_for(body["secret"])}
    )
    assert done.status_code == 200
    return str(body["secret"])


async def test_first_sign_in_sets_up_the_authenticator_and_opens_a_session(
    client: httpx.AsyncClient, admin: AdminUser
) -> None:
    await sign_in(client)
    assert "sym_admin" in client.cookies
    me = await client.get("/admin/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "super_admin"
    async with SessionLocal() as session:
        stored = await session.get(AdminUser, admin.id)
        assert stored is not None and stored.totp_enabled


async def test_later_sign_ins_ask_for_a_code_and_reject_reused_codes(
    client: httpx.AsyncClient, admin: AdminUser
) -> None:
    secret = await sign_in(client)
    client.cookies.clear()

    step = await client.post("/admin/api/auth/password", json={"email": EMAIL, "password": PASSWORD})
    assert step.json()["next"] == "totp"
    assert step.json()["secret"] is None
    reused = await client.post(
        "/admin/api/auth/code", json={"challenge": step.json()["challenge"], "code": code_for(secret)}
    )
    assert reused.status_code == 401

    fresh = await client.post(
        "/admin/api/auth/code",
        json={"challenge": step.json()["challenge"], "code": code_for(secret, step_offset=1)},
    )
    assert fresh.status_code == 200


async def test_wrong_password_gives_the_same_message_as_unknown_email(
    client: httpx.AsyncClient, admin: AdminUser
) -> None:
    wrong = await client.post("/admin/api/auth/password", json={"email": EMAIL, "password": "nope"})
    unknown = await client.post("/admin/api/auth/password", json={"email": "who@x.in", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_account_locks_after_five_failures(client: httpx.AsyncClient, admin: AdminUser) -> None:
    for _ in range(5):
        await client.post("/admin/api/auth/password", json={"email": EMAIL, "password": "nope"})
    locked = await client.post("/admin/api/auth/password", json={"email": EMAIL, "password": PASSWORD})
    assert locked.status_code == 401
    assert "Too many attempts" in locked.json()["detail"]


async def test_requests_without_the_admin_header_are_refused(
    client: httpx.AsyncClient, admin: AdminUser
) -> None:
    response = await client.post(
        "/admin/api/auth/password", json={"email": EMAIL, "password": PASSWORD}, headers={"X-SYM-Admin": "0"}
    )
    assert response.status_code == 403


async def test_tampered_challenge_is_rejected(client: httpx.AsyncClient, admin: AdminUser) -> None:
    step = await client.post("/admin/api/auth/password", json={"email": EMAIL, "password": PASSWORD})
    challenge = step.json()["challenge"]
    forged = challenge[:-2] + ("AA" if not challenge.endswith("AA") else "BB")
    response = await client.post("/admin/api/auth/code", json={"challenge": forged, "code": "123456"})
    assert response.status_code == 401


async def test_sign_out_ends_the_session(client: httpx.AsyncClient, admin: AdminUser) -> None:
    await sign_in(client)
    token = client.cookies["sym_admin"]
    assert (await client.post("/admin/api/auth/sign-out")).status_code == 204
    client.cookies.set("sym_admin", token, path="/admin")
    assert (await client.get("/admin/api/auth/me")).status_code == 401


async def test_sign_ins_and_failures_are_audited(client: httpx.AsyncClient, admin: AdminUser) -> None:
    await client.post("/admin/api/auth/password", json={"email": EMAIL, "password": "nope"})
    await sign_in(client)
    async with SessionLocal() as session:
        actions = list(await session.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))
    assert actions == ["admin.sign_in_failed", "admin.signed_in"]


async def test_system_status_needs_a_session(client: httpx.AsyncClient, admin: AdminUser) -> None:
    assert (await client.get("/admin/api/system/status")).status_code == 401
    await sign_in(client)
    status = await client.get("/admin/api/system/status")
    assert status.status_code == 200
    assert status.json()["web_tables_ok"] is True
    assert status.json()["recent_activity"][0]["action"] == "admin.signed_in"
