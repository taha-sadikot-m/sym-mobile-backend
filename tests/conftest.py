"""Tests run against a throwaway `sym_test` database: the web schema snapshot plus our migrations.

Point TEST_DATABASE_URL at a server where the user may create databases (the docker-compose one by default).
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import asyncpg
import httpx
import pyotp
import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://owner:owner_local@localhost:5433/sym_test"
)

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": TEST_URL,
        "DATABASE_POOLED": "false",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-settings",
        "CONTRACT_CHECK_ON_STARTUP": "false",
        "JWT_SECRET": "test-jwt-secret-that-is-long-enough-for-settings",
        "PASSWORD_HASH_ITERATIONS": "1000",
        "CORS_ORIGINS": "[]",
        "RESEND_API_KEY": "",
        "GEMINI_API_KEY": "",
        "DEEPGRAM_API_KEY": "",
        "LIVEKIT_URL": "",
        "LIVEKIT_API_KEY": "",
        "LIVEKIT_API_SECRET": "",
        "EMAIL_DELIVERY": "log",
        "PUSH_ENABLED": "false",
    }
)


def _dsn(url: str, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def _web_schema_sql() -> str:
    lines = (ROOT / "db" / "web_schema.sql").read_text(encoding="utf-8").splitlines()
    # Drop psql meta-commands (\restrict ...); the rest is plain SQL.
    return "\n".join(line for line in lines if not line.startswith("\\"))


async def _build_database() -> None:
    name = make_url(TEST_URL).database
    admin = await asyncpg.connect(_dsn(TEST_URL, "postgres"))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(_dsn(TEST_URL))
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await conn.execute(_web_schema_sql())
    finally:
        await conn.close()


def pytest_configure(config: pytest.Config) -> None:
    asyncio.run(_build_database())
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        check=True,
        env={**os.environ},
        capture_output=True,
    )


@pytest.fixture(autouse=True)
async def clean_mobile_tables() -> AsyncIterator[None]:
    from app.ai import instructions
    from app.core import email
    from app.modules.catalog.router import clear_cache
    from app.modules.live.router import _rooms

    clear_cache()
    instructions.clear_cache()
    _rooms.clear()
    email.outbox.clear()
    yield
    from sqlalchemy import text

    from app.core.db import engine
    from app.models import Base

    tables = ", ".join(f"mobile.{table.name}" for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables}, public.users CASCADE"))


@pytest.fixture
async def seeded() -> None:
    """The starting content: 195 countries, 30 committees, 275 agendas, 24 topics."""
    from app.content.seeding import seed_all
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        await seed_all(session)
        await session.commit()


@pytest.fixture
def admin_client() -> Callable[[str], Awaitable[httpx.AsyncClient]]:
    """Factory: a signed-in admin console client for the given role."""
    from app.core.db import SessionLocal
    from app.core.passwords import hash_admin_password
    from app.main import app
    from app.models import AdminRole, AdminUser

    clients: list[httpx.AsyncClient] = []

    async def make(role: str = "super_admin") -> httpx.AsyncClient:
        secret = pyotp.random_base32()
        email = f"{role.replace('_', '-')}-{len(clients)}@symedu.in"
        async with SessionLocal() as session:
            session.add(
                AdminUser(
                    email=email,
                    full_name=role.replace("_", " ").title(),
                    role=AdminRole(role),
                    password_hash=hash_admin_password("password-for-tests"),
                    totp_secret=secret,
                    totp_enabled=True,
                )
            )
            await session.commit()
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-SYM-Admin": "1"}
        )
        clients.append(client)
        step = await client.post(
            "/admin/api/auth/password", json={"email": email, "password": "password-for-tests"}
        )
        signed_in = await client.post(
            "/admin/api/auth/code",
            json={"challenge": step.json()["challenge"], "code": pyotp.TOTP(secret).now()},
        )
        assert signed_in.status_code == 200, signed_in.text
        return client

    return make
