"""Finished MUN practice sessions (the report a student saw), kept so progress follows them across devices."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class MunSession(Base):
    __tablename__ = "mun_sessions"

    # The app's run id, so saving the same session twice never creates a duplicate.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="single")
    committee: Mapped[str] = mapped_column(String(160))
    committee_slug: Mapped[str | None] = mapped_column(String(64), index=True)
    agenda: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(120))
    overall_score: Mapped[int] = mapped_column(Integer)
    scores: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    speech_seconds: Mapped[int] = mapped_column(Integer, default=0)
    pois_answered: Mapped[int] = mapped_column(Integer, default=0)
    ai_feedback: Mapped[bool] = mapped_column(default=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
