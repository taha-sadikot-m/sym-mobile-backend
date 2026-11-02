"""Student account data that only the app needs. The account itself lives in the web's `users` table.

`user_id` columns hold `public.users.id` without a foreign key: the web backend (Django) deletes users
with its own cascade logic, and a constraint it doesn't know about would make those deletes fail.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func, literal_column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped, utcnow


class AppStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class ConsentStatus(StrEnum):
    """Parental consent (India's DPDP Act): under-18s need it before live 1:1 debates with other students."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    GRANTED = "granted"


class Profile(Timestamped, Base):
    __tablename__ = "profiles"
    __table_args__ = (
        Index("uq_profiles_username_lower", func.lower(literal_column("username")), unique=True),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(24))
    birth_year: Mapped[int | None] = mapped_column(Integer)
    institution: Mapped[str] = mapped_column(String(160), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    country: Mapped[str] = mapped_column(String(80), default="")
    mun_experience: Mapped[str | None] = mapped_column(String(16))  # beginner / intermediate / advanced
    app_status: Mapped[AppStatus] = mapped_column(String(16), default=AppStatus.ACTIVE)
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    consent_status: Mapped[ConsentStatus] = mapped_column(String(16), default=ConsentStatus.NOT_REQUIRED)
    # The parent or guardian who received the consent code, and when they confirmed.
    parent_email: Mapped[str | None] = mapped_column(String(254))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    """One row per issued refresh token.

    Rotated on every use; reuse of an old one revokes the whole family (that device is signed out).
    """

    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device: Mapped[str | None] = mapped_column(String(160))


class CodePurpose(StrEnum):
    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"  # noqa: S105 - a purpose name, not a password
    PARENT_CONSENT = "parent_consent"


class EmailCode(Base):
    """A 6-digit code sent by email. Stored hashed; limited attempts; one live code per user and purpose."""

    __tablename__ = "email_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    purpose: Mapped[CodePurpose] = mapped_column(String(32))
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimit(Base):
    """Fixed-window counters (e.g. sign-in attempts per email, sign-ups per IP)."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class AccountDeletion(Base):
    """Proof that a deletion request was carried out (no personal data kept)."""

    __tablename__ = "account_deletions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    requested_by: Mapped[str] = mapped_column(String(16))  # "student" or "admin"
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
