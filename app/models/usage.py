"""What the outside AI services were asked to do (the admin dashboard's usage and cost view), and each
student's daily free allowance."""

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class AiCall(Base):
    __tablename__ = "ai_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(16))  # gemini / deepgram
    feature: Mapped[str] = mapped_column(String(48), index=True)  # e.g. mun.feedback
    model: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(String(300))
    # The language the answer was written for (app.ai.comfort), e.g. "ta", or "ta+hi" for two students.
    # Tamil and Hindi script cost more tokens per word, so the dashboard can compare.
    language: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class UsageDaily(Base):
    """Free-plan counters per student per India day (spec 8): scored attempts and Chanakya messages."""

    __tablename__ = "usage_daily"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    scored_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chat_messages: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
