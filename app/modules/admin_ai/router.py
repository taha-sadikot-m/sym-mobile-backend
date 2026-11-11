"""Admin console AI page: guidance per feature, an off switch, a try-it box and the call log."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import case, func, select

from app.ai import instructions
from app.ai.gemini import AiUnavailable
from app.content.settings import AiCosts
from app.core import audit, ratelimit
from app.models import AdminUser, AppSetting
from app.models.base import utcnow
from app.models.usage import AiCall
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import ContentEditor, PageOut
from app.modules.debates import ai as debate_ai

router = APIRouter(prefix="/admin/api/ai", tags=["admin ai"], dependencies=[Depends(require_admin_header)])


class FeatureOut(BaseModel):
    id: str
    label: str


class InstructionsOut(BaseModel):
    value: instructions.AiInstructions
    features: list[FeatureOut]
    max_chars: int
    updated_at: datetime | None
    updated_by: str | None


async def _out(session: DbSession) -> InstructionsOut:
    row = await session.get(AppSetting, instructions.KEY)
    email = (
        await session.scalar(select(AdminUser.email).where(AdminUser.id == row.updated_by))
        if row and row.updated_by
        else None
    )
    return InstructionsOut(
        value=instructions.AiInstructions.model_validate(row.value if row else {}),
        features=[FeatureOut(id=key, label=label) for key, label in instructions.FEATURES.items()],
        max_chars=instructions.MAX_CHARS,
        updated_at=row.updated_at if row else None,
        updated_by=email,
    )


@router.get("/instructions")
async def get_instructions(_: CurrentAdmin, session: DbSession) -> InstructionsOut:
    return await _out(session)


@router.put("/instructions")
async def save_instructions(
    body: dict[str, object], admin: ContentEditor, request: Request, session: DbSession
) -> InstructionsOut:
    try:
        value = instructions.AiInstructions.model_validate(body).model_dump(mode="json")
    except ValidationError as error:
        message = str(error.errors()[0]["msg"]).removeprefix("Value error, ")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message) from error
    row = await session.get(AppSetting, instructions.KEY)
    before = row.value if row else instructions.AiInstructions().model_dump(mode="json")
    if before != value:
        if row is None:
            session.add(AppSetting(key=instructions.KEY, value=value, updated_by=admin.id))
        else:
            row.value = value
            row.updated_by = admin.id
            row.updated_at = utcnow()
        audit.record(
            session,
            "ai.instructions_updated",
            admin_id=admin.id,
            target_type="setting",
            target_id=instructions.KEY,
            ip=client_ip(request),
            changes={
                key: [before.get(key), value.get(key)] for key in value if before.get(key) != value.get(key)
            },
        )
        await session.commit()
        instructions.clear_cache()
    return await _out(session)


class TryIn(BaseModel):
    feature: Literal["debate.reply", "debate.analysis"]
    motion: str = Field(min_length=5, max_length=300)
    student_side: debate_ai.Side = "for"
    difficulty: debate_ai.Difficulty = "medium"
    student_text: str = Field(min_length=10, max_length=3000)


class TryOut(BaseModel):
    feature: str
    reply: str | None = None
    analysis: dict[str, object] | None = None


@router.post("/try")
async def try_it(body: TryIn, admin: ContentEditor) -> TryOut:
    """Run a real AI request with the saved guidance on a sample, so the team can see the effect."""
    await ratelimit.hit(f"admin-ai-try:{admin.id}", limit=30, per_seconds=60 * 60)
    turns = [debate_ai.TurnIn(speaker="user", phase="opening", text=body.student_text)]
    ai_side: debate_ai.Side = "against" if body.student_side == "for" else "for"
    try:
        if body.feature == "debate.reply":
            text = await debate_ai.reply(
                motion=body.motion,
                context="",
                ai_side=ai_side,
                difficulty=body.difficulty,
                phase="rebuttal",
                points=[],
                turns=turns,
                user_id=None,
            )
            return TryOut(feature=body.feature, reply=text)
        result = await debate_ai.analyse(
            motion=body.motion,
            student_side=body.student_side,
            difficulty=body.difficulty,
            turns=turns,
            user_id=None,
        )
        return TryOut(feature=body.feature, analysis=result.model_dump(mode="json"))
    except AiUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"The AI couldn’t answer: {str(error)[:200]}"
        ) from error


class CallRow(BaseModel):
    created_at: datetime
    provider: str
    feature: str
    model: str
    ok: bool
    error: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    audio_seconds: float
    cost_usd: float
    language: str | None = None


class FeatureStats(BaseModel):
    feature: str
    calls: int
    failures: int
    avg_latency_ms: int
    cost_usd: float


class CallsOut(PageOut[CallRow]):
    week: list[FeatureStats]


def _price(rates: AiCosts, tokens_in: float, tokens_out: float, seconds: float) -> float:
    return (
        tokens_in / 1e6 * rates.gemini_input_per_million
        + tokens_out / 1e6 * rates.gemini_output_per_million
        + seconds / 60 * rates.deepgram_per_minute
    )


@router.get("/calls")
async def list_calls(
    _: CurrentAdmin,
    session: DbSession,
    failed: bool = False,
    feature: Annotated[str | None, Query(max_length=48)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CallsOut:
    stored = await session.get(AppSetting, "ai_costs")
    rates = AiCosts.model_validate(stored.value if stored else {})
    stmt = select(AiCall).order_by(AiCall.created_at.desc())
    if failed:
        stmt = stmt.where(AiCall.ok.is_(False))
    if feature:
        stmt = stmt.where(AiCall.feature == feature)
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(await session.scalars(stmt.limit(limit).offset(offset)))
    week = await session.execute(
        select(
            AiCall.feature,
            func.count(),
            func.sum(case((AiCall.ok.is_(False), 1), else_=0)),
            func.coalesce(func.avg(AiCall.latency_ms), 0),
            func.coalesce(func.sum(AiCall.input_tokens), 0),
            func.coalesce(func.sum(AiCall.output_tokens), 0),
            func.coalesce(func.sum(AiCall.audio_seconds), 0.0),
        )
        .where(AiCall.created_at >= utcnow() - timedelta(days=7))
        .group_by(AiCall.feature)
        .order_by(func.count().desc())
    )
    return CallsOut(
        items=[
            CallRow(
                created_at=row.created_at,
                provider=row.provider,
                feature=row.feature,
                model=row.model,
                ok=row.ok,
                error=row.error,
                latency_ms=row.latency_ms,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                audio_seconds=row.audio_seconds,
                language=row.language,
                cost_usd=round(_price(rates, row.input_tokens, row.output_tokens, row.audio_seconds), 5),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        week=[
            FeatureStats(
                feature=name,
                calls=calls,
                failures=int(failures or 0),
                avg_latency_ms=int(latency),
                cost_usd=round(_price(rates, tokens_in, tokens_out, seconds), 4),
            )
            for name, calls, failures, latency, tokens_in, tokens_out, seconds in week
        ],
    )
