"""Regression tests for the security review (2026-10-18)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import update
from starlette.requests import Request

from app.core import email
from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.main import app
from app.web.users import users
from tests.test_student_auth import PASSWORD, auth, signed_up_and_verified


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def test_verify_email_never_signs_in_a_verified_account(client: httpx.AsyncClient) -> None:
    await signed_up_and_verified(client, "victim@school.in")
    takeover = await client.post(
        "/v1/auth/verify-email", json={"email": "victim@school.in", "code": "000000"}
    )
    assert takeover.status_code == 409
    assert "access_token" not in takeover.text


async def test_password_reset_cannot_reopen_a_switched_off_account(client: httpx.AsyncClient) -> None:
    await signed_up_and_verified(client, "banned@school.in")
    async with SessionLocal() as session:
        await session.execute(
            update(users).where(users.c.email == "banned@school.in").values(is_active=False)
        )
        await session.commit()
    sent_before = sum(1 for item in email.outbox if item.to == "banned@school.in")
    await client.post("/v1/auth/forgot-password", json={"email": "banned@school.in"})
    assert sum(1 for item in email.outbox if item.to == "banned@school.in") == sent_before  # no code sent
    reset = await client.post(
        "/v1/auth/reset-password",
        json={"email": "banned@school.in", "code": "123456", "new_password": PASSWORD + "x"},
    )
    assert reset.status_code == 403


async def test_oversized_requests_are_refused(client: httpx.AsyncClient) -> None:
    tokens = await signed_up_and_verified(client, "big@school.in")
    response = await client.patch(
        "/v1/me", headers=auth(tokens) | {"content-length": str(5 * 1024 * 1024)}, content=b"{}"
    )
    assert response.status_code == 413


def _request(peer: str, forwarded: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_client_ip_ignores_addresses_the_client_wrote(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxy_hops", 0)
    assert client_ip(_request("10.0.0.5", "6.6.6.6")) == "10.0.0.5"  # no proxy: header ignored
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    # The client forged "6.6.6.6"; Render appended the real address last.
    assert client_ip(_request("10.0.0.5", "6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(_request("10.0.0.5", "not-an-ip")) == "10.0.0.5"
