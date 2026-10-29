"""The web backend's `users` table: student accounts shared by the website and the app.

Defined as a plain table (not part of our migrated metadata) so Alembic never touches it. Every column we
write is listed in app/web/contract.py and checked at startup.

New accounts are created exactly like the web's personal sign-up: user_type PERSONAL, inactive and
unverified until the email is confirmed. The web only lets PERSONAL accounts sign in when both
`is_active` and `is_email_verified` are true, and so does the app.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow

web_metadata = MetaData(schema="public")

users = Table(
    "users",
    web_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("password", String(128), nullable=False),
    Column("last_login", DateTime(timezone=True)),
    Column("is_superuser", Boolean, nullable=False),
    Column("email", String(255), nullable=False),
    Column("full_name", String(255), nullable=False),
    Column("user_type", String(20), nullable=False),
    Column("member_id", String(50)),
    Column("is_active", Boolean, nullable=False),
    Column("is_email_verified", Boolean, nullable=False),
    Column("is_staff", Boolean, nullable=False),
    Column("must_change_password", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_login_at", DateTime(timezone=True)),
    Column("organization_id", BigInteger),
    Column("has_completed_onboarding", Boolean, nullable=False),
    Column("current_rank", Integer, nullable=False),
    Column("rank_tier", String(20), nullable=False),
    Column("total_debates", Integer, nullable=False),
    Column("total_wins", Integer, nullable=False),
    Column("win_rate", Numeric(5, 2), nullable=False),
    Column("current_win_streak", Integer, nullable=False),
)

# Account types that may use the app. Admin and developer accounts belong on the website.
APP_USER_TYPES = ("PERSONAL", "ORGANIZATION")


@dataclass(frozen=True)
class WebUser:
    id: int
    email: str
    full_name: str
    password: str
    user_type: str
    is_active: bool
    is_email_verified: bool
    organization_id: int | None
    current_rank: int
    rank_tier: str
    created_at: datetime


COLUMNS = [getattr(users.c, name) for name in WebUser.__dataclass_fields__]
_COLUMNS = COLUMNS


def normalize_email(email: str) -> str:
    """Django's rule: lower-case the domain, keep the local part as typed."""
    local, _, domain = email.strip().rpartition("@")
    return f"{local}@{domain.lower()}" if local else email.strip()


async def by_email(session: AsyncSession, email: str) -> WebUser | None:
    """Exact match first (how the web looks accounts up), then case-insensitive."""
    normalized = normalize_email(email)
    row = (await session.execute(select(*_COLUMNS).where(users.c.email == normalized))).first()
    if row is None:
        row = (
            await session.execute(
                select(*_COLUMNS)
                .where(func.lower(users.c.email) == normalized.lower())
                .order_by(users.c.id)
                .limit(1)
            )
        ).first()
    return WebUser(*row) if row else None


async def by_id(session: AsyncSession, user_id: int) -> WebUser | None:
    row = (await session.execute(select(*_COLUMNS).where(users.c.id == user_id))).first()
    return WebUser(*row) if row else None


async def email_taken(session: AsyncSession, email: str) -> bool:
    return await by_email(session, email) is not None


async def create(session: AsyncSession, *, email: str, full_name: str, password_hash: str) -> int:
    now = utcnow()
    user_id = await session.scalar(
        insert(users)
        .values(
            password=password_hash,
            last_login=None,
            is_superuser=False,
            email=normalize_email(email),
            full_name=full_name.strip(),
            user_type="PERSONAL",
            member_id=None,
            is_active=False,
            is_email_verified=False,
            is_staff=False,
            must_change_password=False,
            created_at=now,
            updated_at=now,
            last_login_at=None,
            organization_id=None,
            has_completed_onboarding=False,
            current_rank=1000,
            rank_tier="BEGINNER",
            total_debates=0,
            total_wins=0,
            win_rate=Decimal("0.00"),
            current_win_streak=0,
        )
        .returning(users.c.id)
    )
    assert user_id is not None
    return int(user_id)


async def set_fields(session: AsyncSession, user_id: int, **values: object) -> None:
    await session.execute(update(users).where(users.c.id == user_id).values(**values, updated_at=utcnow()))


async def mark_verified(session: AsyncSession, user_id: int) -> None:
    await set_fields(session, user_id, is_active=True, is_email_verified=True)


async def record_login(session: AsyncSession, user_id: int) -> None:
    now = utcnow()
    await set_fields(session, user_id, last_login=now, last_login_at=now)
