"""Practice and AI usage numbers for the admin dashboard. Totals only — no per-student profiling."""

from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Integer, case, cast, func, select

from app.content.settings import AiCosts
from app.models import AppSetting
from app.models.base import utcnow
from app.models.mun import MunSession
from app.models.usage import AiCall
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, require_admin_header
from app.web.debates import debate_sessions

router = APIRouter(
    prefix="/admin/api/insights", tags=["admin insights"], dependencies=[Depends(require_admin_header)]
)


class CommitteeCount(BaseModel):
    committee: str
    sessions: int
    average_score: int


class FeatureUsage(BaseModel):
    feature: str
    calls: int
    failures: int
    average_ms: int


class Practice(BaseModel):
    sessions_total: int
    sessions_this_week: int
    students_practising_this_week: int
    average_score: int | None
    ai_feedback_share: int  # percent of sessions scored by the AI coach
    debates_this_week: int  # 1:1 debates played in the app (saved to the website tables)
    top_committees: list[CommitteeCount]


class AiUsage(BaseModel):
    calls_today: int
    failures_today: int
    cost_today_usd: float
    cost_week_usd: float
    daily_budget_usd: float
    over_budget: bool
    by_feature: list[FeatureUsage]


class Insights(BaseModel):
    practice: Practice
    ai: AiUsage


async def _cost(session: DbSession, since: object, rates: AiCosts) -> float:
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(AiCall.input_tokens), 0),
                func.coalesce(func.sum(AiCall.output_tokens), 0),
                func.coalesce(func.sum(AiCall.audio_seconds), 0.0),
            ).where(AiCall.created_at >= since)
        )
    ).one()
    tokens_in, tokens_out, seconds = row
    return round(
        tokens_in / 1e6 * rates.gemini_input_per_million
        + tokens_out / 1e6 * rates.gemini_output_per_million
        + seconds / 60 * rates.deepgram_per_minute,
        2,
    )


@router.get("")
async def insights(_: CurrentAdmin, session: DbSession) -> Insights:
    now = utcnow()
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(days=1)
    stored = await session.get(AppSetting, "ai_costs")
    rates = AiCosts.model_validate(stored.value if stored else {})

    totals = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(MunSession.completed_at >= week_ago),
                func.count(func.distinct(MunSession.user_id)).filter(MunSession.completed_at >= week_ago),
                func.avg(MunSession.overall_score),
                func.sum(cast(MunSession.ai_feedback, Integer)),
            )
        )
    ).one()
    total, week, students, average, ai_scored = totals
    top = await session.execute(
        select(MunSession.committee, func.count().label("n"), func.avg(MunSession.overall_score))
        .group_by(MunSession.committee)
        .order_by(func.count().desc())
        .limit(5)
    )
    features = await session.execute(
        select(
            AiCall.feature,
            func.count(),
            func.sum(case((AiCall.ok.is_(False), 1), else_=0)),
            func.avg(AiCall.latency_ms),
        )
        .where(AiCall.created_at >= week_ago)
        .group_by(AiCall.feature)
        .order_by(func.count().desc())
    )
    today = (
        await session.execute(
            select(func.count(), func.sum(case((AiCall.ok.is_(False), 1), else_=0))).where(
                AiCall.created_at >= day_ago
            )
        )
    ).one()
    cost_today = await _cost(session, day_ago, rates)
    debates_week = await session.scalar(
        select(func.count()).where(
            debate_sessions.c.metadata["source"].astext == "mobile", debate_sessions.c.created_at >= week_ago
        )
    )
    return Insights(
        practice=Practice(
            sessions_total=total,
            sessions_this_week=week,
            students_practising_this_week=students,
            average_score=round(average) if average is not None else None,
            ai_feedback_share=round(100 * (ai_scored or 0) / total) if total else 0,
            debates_this_week=debates_week or 0,
            top_committees=[
                CommitteeCount(committee=name, sessions=count, average_score=round(avg or 0))
                for name, count, avg in top
            ],
        ),
        ai=AiUsage(
            calls_today=today[0] or 0,
            failures_today=today[1] or 0,
            cost_today_usd=cost_today,
            cost_week_usd=await _cost(session, week_ago, rates),
            daily_budget_usd=rates.daily_budget_usd,
            over_budget=cost_today > rates.daily_budget_usd,
            by_feature=[
                FeatureUsage(feature=name, calls=calls, failures=failures or 0, average_ms=round(avg or 0))
                for name, calls, failures, avg in features
            ],
        ),
    )
