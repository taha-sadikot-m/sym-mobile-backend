import re
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from sqlalchemy import select

from app.core import email
from app.core.db import SessionLocal
from app.core.passwords import check_django_password, make_django_password
from app.main import app
from app.models import AccountDeletion, Profile
from app.web import users as web_users
from app.web.users import users

AdminClient = Callable[[str], Awaitable[httpx.AsyncClient]]
PASSWORD = "Debate-Champion-26"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


def last_code(to: str) -> str:
    message = next(item for item in reversed(email.outbox) if item.to.lower() == to.lower())
    match = re.search(r"\b(\d{6})\b", message.text)
    assert match
    return match.group(1)


async def sign_up(
    client: httpx.AsyncClient, address: str = "riya@school.in", **extra: object
) -> httpx.Response:
    body = {
        "full_name": "Riya Mehta",
        "email": address,
        "password": PASSWORD,
        "age": 15,
        "institution": "DPS",
    } | extra
    return await client.post("/v1/auth/sign-up", json=body)


async def signed_up_and_verified(
    client: httpx.AsyncClient, address: str = "riya@school.in"
) -> dict[str, str]:
    assert (await sign_up(client, address)).status_code == 201
    verified = await client.post("/v1/auth/verify-email", json={"email": address, "code": last_code(address)})
    assert verified.status_code == 200, verified.text
    return verified.json()


def auth(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def insert_web_user(address: str, *, user_type: str = "PERSONAL", verified: bool = True) -> int:
    """An account created on the website (Django), before the app existed."""
    async with SessionLocal() as session:
        user_id = await web_users.create(
            session,
            email=address,
            full_name="Arjun Rao",
            password_hash=make_django_password(PASSWORD, iterations=1000),
        )
        await web_users.set_fields(
            session, user_id, user_type=user_type, is_active=verified, is_email_verified=verified
        )
        await session.commit()
    return user_id


async def test_sign_up_creates_a_website_style_account_that_needs_confirming(
    client: httpx.AsyncClient,
) -> None:
    response = await sign_up(client, "Riya@School.IN")
    assert response.status_code == 201
    assert response.json() == {"status": "verification_required", "email": "Riya@school.in"}
    async with SessionLocal() as session:
        row = (await session.execute(select(users).where(users.c.email == "Riya@school.in"))).one()
    assert (row.user_type, row.is_active, row.is_email_verified) == ("PERSONAL", False, False)
    assert row.password.startswith("pbkdf2_sha256$")
    assert check_django_password(PASSWORD, row.password)

    blocked = await client.post("/v1/auth/sign-in", json={"email": "riya@school.in", "password": PASSWORD})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "email_unverified"


async def test_verifying_the_email_activates_the_account_and_signs_in(client: httpx.AsyncClient) -> None:
    await sign_up(client)
    wrong = await client.post("/v1/auth/verify-email", json={"email": "riya@school.in", "code": "000000"})
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "code_wrong"

    tokens = await client.post(
        "/v1/auth/verify-email", json={"email": "riya@school.in", "code": last_code("riya@school.in")}
    )
    assert tokens.status_code == 200
    me = (await client.get("/v1/me", headers=auth(tokens.json()))).json()
    assert me["full_name"] == "Riya Mehta" and me["institution"] == "DPS" and me["age"] == 15
    async with SessionLocal() as session:
        row = (await session.execute(select(users.c.is_active, users.c.is_email_verified))).one()
    assert tuple(row) == (True, True)


async def test_website_accounts_sign_in_to_the_app(client: httpx.AsyncClient) -> None:
    user_id = await insert_web_user("arjun@web.in")
    response = await client.post("/v1/auth/sign-in", json={"email": "ARJUN@web.in", "password": PASSWORD})
    assert response.status_code == 200
    async with SessionLocal() as session:
        assert await session.get(Profile, user_id) is not None  # created on first app sign-in


async def test_admin_and_developer_accounts_stay_on_the_website(client: httpx.AsyncClient) -> None:
    await insert_web_user("admin@web.in", user_type="ORG_ADMIN")
    response = await client.post("/v1/auth/sign-in", json={"email": "admin@web.in", "password": PASSWORD})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "wrong_account_type"


async def test_duplicate_emails_and_weak_passwords_are_refused(client: httpx.AsyncClient) -> None:
    await insert_web_user("arjun@web.in")
    taken = await sign_up(client, "Arjun@WEB.in")
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "email_taken"
    weak = await sign_up(client, "new@school.in", password="12345678")
    assert weak.status_code == 422
    assert weak.json()["detail"]["message"] == "Use letters as well as numbers."
    similar = await sign_up(client, "riyamehta@school.in", password="riyamehta1")
    assert "similar" in similar.json()["detail"]["message"]


async def test_wrong_password_and_unknown_email_look_the_same(client: httpx.AsyncClient) -> None:
    await insert_web_user("arjun@web.in")
    wrong = await client.post("/v1/auth/sign-in", json={"email": "arjun@web.in", "password": "Nope-Nope-1"})
    unknown = await client.post("/v1/auth/sign-in", json={"email": "ghost@web.in", "password": "Nope-Nope-1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_refresh_tokens_rotate_and_reuse_signs_the_device_out(client: httpx.AsyncClient) -> None:
    first = await signed_up_and_verified(client)
    second = (await client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})).json()
    assert second["refresh_token"] != first["refresh_token"]

    replay = await client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    after_theft = await client.post("/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert after_theft.status_code == 401


async def test_sign_out_ends_the_refresh_token(client: httpx.AsyncClient) -> None:
    tokens = await signed_up_and_verified(client)
    assert (
        await client.post("/v1/auth/sign-out", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 204
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401


async def test_password_reset_with_an_emailed_code(client: httpx.AsyncClient) -> None:
    await insert_web_user("arjun@web.in")
    unknown = await client.post("/v1/auth/forgot-password", json={"email": "ghost@web.in"})
    known = await client.post("/v1/auth/forgot-password", json={"email": "arjun@web.in"})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json()

    reset = await client.post(
        "/v1/auth/reset-password",
        json={
            "email": "arjun@web.in",
            "code": last_code("arjun@web.in"),
            "new_password": "Brand-New-Motion-7",
        },
    )
    assert reset.status_code == 200
    old = await client.post("/v1/auth/sign-in", json={"email": "arjun@web.in", "password": PASSWORD})
    new = await client.post(
        "/v1/auth/sign-in", json={"email": "arjun@web.in", "password": "Brand-New-Motion-7"}
    )
    assert (old.status_code, new.status_code) == (401, 200)


async def test_codes_stop_working_after_five_wrong_tries(client: httpx.AsyncClient) -> None:
    await sign_up(client)
    code = last_code("riya@school.in")
    for _ in range(5):
        await client.post("/v1/auth/verify-email", json={"email": "riya@school.in", "code": "111111"})
    locked = await client.post("/v1/auth/verify-email", json={"email": "riya@school.in", "code": code})
    assert locked.status_code == 400 and locked.json()["detail"]["code"] == "code_expired"


async def test_sign_in_is_rate_limited_per_email(client: httpx.AsyncClient) -> None:
    await insert_web_user("arjun@web.in")
    for _ in range(10):
        await client.post("/v1/auth/sign-in", json={"email": "arjun@web.in", "password": "Wrong-Pass-1"})
    limited = await client.post("/v1/auth/sign-in", json={"email": "arjun@web.in", "password": PASSWORD})
    assert limited.status_code == 429


async def test_profile_updates_and_unique_usernames(client: httpx.AsyncClient) -> None:
    riya = await signed_up_and_verified(client)
    other = await signed_up_and_verified(client, "kabir@school.in")
    taken = await client.patch("/v1/me", headers=auth(other), json={"username": "riya"})
    assert taken.status_code == 200
    clash = await client.patch("/v1/me", headers=auth(riya), json={"username": "RIYA"})
    assert clash.status_code == 409
    bad = await client.patch("/v1/me", headers=auth(riya), json={"username": "a b"})
    assert bad.status_code == 422

    renamed = await client.patch("/v1/me", headers=auth(riya), json={"full_name": "Riya M.", "city": "Pune"})
    assert renamed.json()["full_name"] == "Riya M." and renamed.json()["city"] == "Pune"
    check = await client.get("/v1/usernames/@Riya", headers=auth(riya))
    assert check.json() == {"username": "riya", "available": False}


async def test_change_password_needs_the_current_one(client: httpx.AsyncClient) -> None:
    tokens = await signed_up_and_verified(client)
    wrong = await client.post(
        "/v1/me/password",
        headers=auth(tokens),
        json={"current_password": "nope", "new_password": "Another-Strong-9"},
    )
    assert wrong.status_code == 400
    ok = await client.post(
        "/v1/me/password",
        headers=auth(tokens),
        json={"current_password": PASSWORD, "new_password": "Another-Strong-9"},
    )
    assert ok.status_code == 204
    # Other devices are signed out.
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401


async def test_deleting_the_account_erases_personal_data(client: httpx.AsyncClient) -> None:
    tokens = await signed_up_and_verified(client)
    assert (
        await client.request("DELETE", "/v1/me", headers=auth(tokens), json={"password": "nope"})
    ).status_code == 400
    assert (
        await client.request("DELETE", "/v1/me", headers=auth(tokens), json={"password": PASSWORD})
    ).status_code == 204

    async with SessionLocal() as session:
        row = (await session.execute(select(users))).one()
        assert (
            row.full_name == "Deleted user" and row.email.endswith("@deleted.invalid") and not row.is_active
        )
        assert row.password.startswith("!")
        assert await session.get(Profile, row.id) is None
        assert await session.scalar(select(AccountDeletion.user_id)) == row.id
    again = await client.post("/v1/auth/sign-in", json={"email": "riya@school.in", "password": PASSWORD})
    assert again.status_code == 401
    assert (await client.get("/v1/me", headers=auth(tokens))).status_code == 403


async def test_requests_without_a_token_are_refused(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/me")
    assert response.status_code == 401 and response.json()["detail"]["code"] == "signed_out"
    forged = await client.get("/v1/me", headers={"Authorization": "Bearer not.a.token"})
    assert forged.status_code == 401


async def test_moderators_can_pause_app_access(client: httpx.AsyncClient, admin_client: AdminClient) -> None:
    tokens = await signed_up_and_verified(client)
    me = (await client.get("/v1/me", headers=auth(tokens))).json()

    analyst = await admin_client("analyst")
    # Analysts see aggregate numbers only, never students' personal details.
    assert (await analyst.get("/admin/api/students", params={"q": "riya"})).status_code == 403
    assert (await analyst.get(f"/admin/api/students/{me['id']}")).status_code == 403
    assert (
        await analyst.post(f"/admin/api/students/{me['id']}/suspend", json={"reason": "spam"})
    ).status_code == 403

    moderator = await admin_client("moderator")
    listed = (await moderator.get("/admin/api/students", params={"q": "riya"})).json()
    assert listed["total"] == 1 and listed["items"][0]["email"] == "riya@school.in"
    paused = await moderator.post(
        f"/admin/api/students/{me['id']}/suspend", json={"reason": "Abusive in a live debate"}
    )
    assert paused.json()["app_status"] == "suspended"
    blocked = await client.get("/v1/me", headers=auth(tokens))
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "app_suspended"
    sign_in = await client.post("/v1/auth/sign-in", json={"email": "riya@school.in", "password": PASSWORD})
    assert sign_in.json()["detail"]["code"] == "app_suspended"

    assert (await moderator.post(f"/admin/api/students/{me['id']}/restore")).json()["app_status"] == "active"
    assert (
        await client.post("/v1/auth/sign-in", json={"email": "riya@school.in", "password": PASSWORD})
    ).status_code == 200

    stats = (await analyst.get("/admin/api/students/stats")).json()
    assert stats == {"total": 1, "new_this_week": 1, "active_this_week": 1, "suspended": 0}
