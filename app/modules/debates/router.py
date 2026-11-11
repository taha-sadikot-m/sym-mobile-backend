"""MyDebate for the app: the prep screen, the AI opponent, analysis, and saving to the website's history."""

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.ai import comfort
from app.ai.gemini import AiUnavailable
from app.core import ratelimit, signing
from app.core.config import get_settings
from app.modules.auth.deps import CurrentStudent, Session
from app.modules.debates import ai
from app.web import debates as web_debates

router = APIRouter(prefix="/v1/debates", tags=["debates"])

DAY = 24 * 60 * 60
SEAL = "debate-analysis"
# Without a server-signed AI analysis, a practice debate still counts but can't be a win, and its
# scores are capped, so nobody can inflate their website stats by posting made-up scores.
UNSEALED_SCORE_CAP = 70


def _unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"message": "The AI opponent is busy; using the built-in opponent.", "code": "ai_unavailable"},
    )


class TopicIn(BaseModel):
    title: str = Field(min_length=5, max_length=300)
    context: str = Field("", max_length=600)
    for_points: list[str] = Field(default_factory=list, max_length=6)
    against_points: list[str] = Field(default_factory=list, max_length=6)


class TurnIn(ai.TurnIn):
    text: str = Field(min_length=1, max_length=8000)


class ReplyIn(BaseModel):
    topic: TopicIn
    student_side: ai.Side
    difficulty: ai.Difficulty
    phase: ai.Phase
    turns: list[TurnIn] = Field(default_factory=list, max_length=40)


class ReplyOut(BaseModel):
    text: str


@router.post("/ai/reply")
async def ai_reply(body: ReplyIn, student: CurrentStudent) -> ReplyOut:
    await ratelimit.hit(f"ai:debate-reply:{student.id}", limit=300, per_seconds=DAY)
    ai_side: ai.Side = "against" if body.student_side == "for" else "for"
    try:
        text = await ai.reply(
            motion=body.topic.title,
            context=body.topic.context,
            ai_side=ai_side,
            difficulty=body.difficulty,
            phase=body.phase,
            points=body.topic.for_points if ai_side == "for" else body.topic.against_points,
            turns=list(body.turns),
            user_id=student.id,
        )
    except AiUnavailable as error:
        raise _unavailable() from error
    return ReplyOut(text=text)


class AnalysisIn(BaseModel):
    topic: TopicIn
    student_side: ai.Side
    difficulty: ai.Difficulty
    turns: list[TurnIn] = Field(min_length=1, max_length=40)


class CategoryOut(BaseModel):
    id: ai.CategoryId
    label: str
    score: int
    strengths: list[str]
    improvements: list[str]
    why: str


class MomentOut(BaseModel):
    turn_number: int
    kind: Literal["best", "weakest"]
    quote: str
    note: str
    better: str | None = Field(None, description="Weakest moment: a stronger way to say it (English)")
    meaning: str | None = Field(None, description="The meaning of `better` in the comfort language")


class AnalysisOut(BaseModel):
    overall_score: int
    opponent_score: int
    verdict: Literal["won", "close", "lost"]
    summary: str
    categories: list[CategoryOut]
    key_moments: list[MomentOut]
    missed_arguments: list[str]
    next_steps: list[str]
    seal: str = Field(
        "", description="Server signature over the scores and verdict; send it back when saving"
    )
    language: comfort.ComfortLanguage = Field(
        "en", description="The comfort language the explanations are written in"
    )


LABELS = {
    "argument": "Argument strength",
    "rebuttal": "Rebuttal & clash",
    "evidence": "Evidence & reasoning",
    "structure": "Structure",
    "delivery": "Delivery & clarity",
}
ORDER = list(LABELS)


def verdict(overall: int, opponent: int) -> Literal["won", "close", "lost"]:
    if overall >= opponent + 5:
        return "won"
    if overall <= opponent - 5:
        return "lost"
    return "close"


@router.post("/ai/analysis")
async def ai_analysis(body: AnalysisIn, student: CurrentStudent) -> AnalysisOut:
    await ratelimit.hit(f"ai:debate-analysis:{student.id}", limit=40, per_seconds=DAY)
    language = comfort.normalise(student.profile.comfort_language)
    try:
        result = await ai.analyse(
            motion=body.topic.title,
            student_side=body.student_side,
            difficulty=body.difficulty,
            turns=list(body.turns),
            user_id=student.id,
            language=language,
        )
    except AiUnavailable as error:
        raise _unavailable() from error
    categories = sorted(result.categories, key=lambda item: ORDER.index(item.id))
    overall = round(sum(item.score for item in categories) / len(categories))
    decided = verdict(overall, result.opponent_score)
    seal = signing.sign(
        {
            "uid": student.id,
            "scores": {item.id: item.score for item in categories},
            "overall": overall,
            "opponent": result.opponent_score,
            "verdict": decided,
        },
        secret=get_settings().secret_key,
        purpose=SEAL,
        ttl_seconds=2 * DAY,
    )
    return AnalysisOut(
        seal=seal,
        overall_score=overall,
        opponent_score=result.opponent_score,
        verdict=decided,
        summary=result.summary,
        categories=[CategoryOut(label=LABELS[item.id], **item.model_dump()) for item in categories],
        key_moments=[MomentOut.model_validate(item.model_dump()) for item in result.key_moments],
        missed_arguments=result.missed_arguments[:3],
        next_steps=result.next_steps[:4],
        language=language,
    )


# ---------- The 60-second prep screen ----------


class PrepIn(BaseModel):
    motion: str = Field(min_length=5, max_length=300)
    side: ai.Side


class HintOut(BaseModel):
    point: str
    why: str


class PrepOut(BaseModel):
    explanation: str = Field(description="In the comfort language")
    hints: list[HintOut] = Field(description="Three 'Build your case' hints, in the comfort language")
    explanation_en: str
    hints_en: list[HintOut]
    language: comfort.ComfortLanguage


@router.post("/prep")
async def prep(body: PrepIn, student: CurrentStudent) -> PrepOut:
    """The motion explained and three hints for your side, also in English (the "Show in English" toggle)."""
    await ratelimit.hit(f"ai:debate-prep:{student.id}", limit=100, per_seconds=DAY)
    language = comfort.normalise(student.profile.comfort_language)
    try:
        result = await ai.prep(motion=body.motion, side=body.side, language=language, user_id=student.id)
    except AiUnavailable as error:
        raise _unavailable() from error
    return PrepOut(
        explanation=result.explanation,
        hints=[HintOut(point=item.point, why=item.why) for item in result.hints],
        explanation_en=result.explanation_en,
        hints_en=[HintOut(point=item.point, why=item.why) for item in result.hints_en],
        language=language,
    )


# ---------- Saving to the website's debate history ----------


class SavedTurn(BaseModel):
    id: uuid.UUID
    speaker: Literal["user", "ai"]
    round: int = Field(ge=1, le=40)
    phase: ai.Phase
    text: str = Field(min_length=1, max_length=8000)
    input: Literal["voice", "text"] = "text"
    duration_ms: int | None = Field(None, ge=0, le=20 * 60 * 1000)
    created_at: datetime


class SaveIn(BaseModel):
    topic: str = Field(min_length=5, max_length=300)
    student_side: ai.Side
    difficulty: ai.Difficulty
    started_at: datetime
    ended_at: datetime
    turns: list[SavedTurn] = Field(min_length=1, max_length=40)
    scores: dict[str, int] = Field(description="The five category scores, 0–100")
    overall_score: int = Field(ge=0, le=100)
    opponent_score: int = Field(ge=0, le=100)
    verdict: Literal["won", "close", "lost"]
    summary: str = Field("", max_length=2000)
    next_steps: list[str] = Field(default_factory=list, max_length=6)
    analysis: dict[str, Any] = Field(default_factory=dict)
    ai_analysis: bool = False
    seal: str = Field("", max_length=2000)

    @field_validator("analysis")
    @classmethod
    def _small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > 40_000:
            raise ValueError("That analysis is too large.")
        return value


def sealed(body: SaveIn, user_id: int) -> bool:
    """True when the scores and verdict are exactly what the server's AI analysis signed for this student."""
    if not body.seal:
        return False
    try:
        claim = signing.unsign(body.seal, secret=get_settings().secret_key, purpose=SEAL)
    except signing.BadToken:
        return False
    return (
        claim.get("uid") == user_id
        and claim.get("scores") == {key: body.scores.get(key) for key in ORDER}
        and claim.get("overall") == body.overall_score
        and claim.get("opponent") == body.opponent_score
        and claim.get("verdict") == body.verdict
    )


class SaveOut(BaseModel):
    saved: bool
    already_saved: bool


def web_scores(scores: dict[str, int]) -> tuple[float, float, float]:
    """SYM's five categories on the website's three: logical, emotional (delivery), rebuttal."""

    def get(key: str) -> float:
        return float(max(0, min(100, scores.get(key, 0))))

    return (
        round((get("argument") + get("evidence") + get("structure")) / 3, 2),
        get("delivery"),
        get("rebuttal"),
    )


@router.put("/ai/sessions/{debate_id}")
async def save_ai_debate(
    debate_id: uuid.UUID, body: SaveIn, student: CurrentStudent, session: Session
) -> SaveOut:
    """Save a finished practice debate into the website history and stats. Saving twice is a no-op."""
    await ratelimit.hit(f"debate-save:{student.id}", limit=30, per_seconds=DAY)
    owner = await session.scalar(
        select(web_debates.debate_sessions.c.creator_id).where(web_debates.debate_sessions.c.id == debate_id)
    )
    if owner is not None:
        if owner != student.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"message": "That debate belongs to someone else.", "code": "conflict"},
            )
        return SaveOut(saved=True, already_saved=True)
    trusted = sealed(body, student.id)
    scores = (
        body.scores
        if trusted
        else {key: min(value, UNSEALED_SCORE_CAP) for key, value in body.scores.items()}
    )
    creator = web_scores(scores)
    winner = student.id if trusted and body.verdict == "won" else None
    await web_debates.record_debate(
        session,
        debate_id=debate_id,
        mode="PRACTICE",
        creator_id=student.id,
        opponent_id=None,
        topic=body.topic,
        creator_for=body.student_side == "for",
        difficulty=body.difficulty.upper(),
        started_at=body.started_at,
        ended_at=body.ended_at,
        messages=[
            {
                "id": turn.id,
                "sender_id": student.id if turn.speaker == "user" else None,
                "text": turn.text,
                "round": turn.round,
                "phase": turn.phase,
                "input": turn.input,
                "duration_ms": turn.duration_ms,
                "created_at": turn.created_at,
            }
            for turn in body.turns
        ],
        metadata={"app_difficulty": body.difficulty, "ai_analysis": trusted},
    )
    notes = body.summary + (
        "\n\nNext steps:\n" + "\n".join(f"• {step}" for step in body.next_steps) if body.next_steps else ""
    )
    opponent = float(body.opponent_score)
    await web_debates.record_result(
        session,
        debate_id=debate_id,
        winner_id=winner,
        creator=creator,
        opponent=(opponent, opponent, opponent),
        judge_notes=notes.strip() or "Practice debate from the SYM app.",
        metadata={
            "verdict": body.verdict,
            "overall_score": body.overall_score,
            "analysis": body.analysis,
            "verified": trusted,
        },
    )
    await web_debates.update_stats(
        session, user_id=student.id, mode="PRACTICE", winner_id=winner, scores=creator
    )
    await session.commit()
    return SaveOut(saved=True, already_saved=False)
