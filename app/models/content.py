"""Content the admin console manages and the app reads.

Committees, agendas, countries, committee seats, debate topics and settings.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped, utcnow


class ContentStatus(StrEnum):
    """Only `published` content reaches the app."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class SeatType(StrEnum):
    COUNTRY = "country"
    PORTFOLIO = "portfolio"
    JOURNALIST = "journalist"
    COURT_ROLE = "court_role"


class SeatRule(StrEnum):
    ALL_UN_MEMBERS = "all_un_members"
    SELECTED = "selected"


class Difficulty(StrEnum):
    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class Committee(Timestamped, Base):
    __tablename__ = "committees"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    abbr: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(64))
    seat_type: Mapped[SeatType] = mapped_column(String(16), default=SeatType.COUNTRY)
    difficulty: Mapped[Difficulty] = mapped_column(String(16), default=Difficulty.BEGINNER)
    description: Mapped[str] = mapped_column(Text, default="")
    decision_rule: Mapped[str] = mapped_column(Text, default="")
    binding: Mapped[str] = mapped_column(Text, default="")
    typical_seats: Mapped[int | None] = mapped_column(Integer)
    seat_rule: Mapped[SeatRule] = mapped_column(String(16), default=SeatRule.ALL_UN_MEMBERS)
    themes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    mymun_upcoming_conferences: Mapped[int | None] = mapped_column(Integer)
    mymun_topic_count: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ContentStatus] = mapped_column(String(16), default=ContentStatus.DRAFT, index=True)


class Agenda(Timestamped, Base):
    __tablename__ = "agendas"
    __table_args__ = (UniqueConstraint("committee_id", "slug"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    committee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("committees.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(96))
    title: Mapped[str] = mapped_column(String(300))
    theme: Mapped[str | None] = mapped_column(String(120))
    # Where it came from: "recent" / "theme example" (MyMUN), "sym" (written for SYM), "admin".
    kind: Mapped[str] = mapped_column(String(32), default="admin")
    seen_at: Mapped[str | None] = mapped_column(String(200))
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    caucus_topics: Mapped[list[str]] = mapped_column(JSONB, default=list)
    key_frameworks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ContentStatus] = mapped_column(String(16), default=ContentStatus.DRAFT, index=True)


class Country(Timestamped, Base):
    __tablename__ = "countries"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)  # ISO 3166-1 alpha-2, lower case
    name: Mapped[str] = mapped_column(String(120), unique=True)
    region: Mapped[str] = mapped_column(String(64))
    is_un_member: Mapped[bool] = mapped_column(Boolean, default=True)
    groupings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CommitteeSeat(Base):
    """Countries seated on a committee whose seat rule is `selected`."""

    __tablename__ = "committee_seats"

    committee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("committees.id", ondelete="CASCADE"), primary_key=True
    )
    country_code: Mapped[str] = mapped_column(
        ForeignKey("countries.code", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )


class DebateTopic(Timestamped, Base):
    __tablename__ = "debate_topics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(96), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(32), index=True)
    context: Mapped[str] = mapped_column(Text, default="")
    # Short claims the AI opponent argues from.
    for_points: Mapped[list[str]] = mapped_column(JSONB, default=list)
    against_points: Mapped[list[str]] = mapped_column(JSONB, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ContentStatus] = mapped_column(String(16), default=ContentStatus.DRAFT, index=True)


class AppSetting(Base):
    """One row per settings group (see app/content/settings.py for the shapes and defaults)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
