"""Live 1:1 debates between students.

Challenges, the room's event log, results, and safety (reports, blocks).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped, utcnow


class ChallengeStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    COUNTERED = "countered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    LIVE = "live"
    COMPLETED = "completed"


class Challenge(Timestamped, Base):
    __tablename__ = "challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(8), unique=True)
    challenger_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # Empty for an open invite link until someone claims it.
    opponent_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    topic: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {id, title, category, context}
    format: Mapped[str] = mapped_column(String(16))
    side_preference: Mapped[str] = mapped_column(String(8))  # for / against / toss
    challenger_side: Mapped[str | None] = mapped_column(String(8))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[ChallengeStatus] = mapped_column(String(16), default=ChallengeStatus.PENDING, index=True)
    countered_by: Mapped[int | None] = mapped_column(BigInteger)
    counter_used: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder: Mapped[bool] = mapped_column(Boolean, default=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    challenger_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opponent_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the first cue begins; the room clock is computed from this and the event log.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The format exactly as it was when the room started, so admin edits never change a running debate.
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The finished debate in the website's debate_sessions table.
    web_debate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LiveEvent(Base):
    """Append-only room log: handovers, time-outs, leaving, and each turn's transcript."""

    __tablename__ = "live_events"
    __table_args__ = (Index("ix_live_events_challenge_seq", "challenge_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # handover / timeout / leave / turn
    user_id: Mapped[int] = mapped_column(BigInteger)
    slot: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LiveResult(Base):
    __tablename__ = "live_results"

    challenge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True
    )
    judge: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Each debater's private report, keyed by user id; each student only ever receives their own.
    reports: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sides: Mapped[dict[str, str]] = mapped_column(JSONB)
    judged_by: Mapped[str] = mapped_column(String(8))  # ai / app
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportStatus(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    ACTIONED = "actioned"


class UserReport(Base):
    """A student reporting another student (required for live voice between users)."""

    __tablename__ = "user_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reporter_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reported_id: Mapped[int] = mapped_column(BigInteger, index=True)
    challenge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("challenges.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(32))
    details: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ReportStatus] = mapped_column(String(16), default=ReportStatus.OPEN, index=True)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Block(Base):
    __tablename__ = "blocks"

    blocker_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    blocked_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
