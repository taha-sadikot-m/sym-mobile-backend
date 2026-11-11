"""Chanakya, the speaking coach, for the app: chat turns, the "Before you record" card, scoring a practice
attempt, and today's free allowance.

Everything answers in the student's comfort language (set on the profile). When Gemini fails or AI is
switched off, the endpoints return 503 `ai_unavailable` and the app uses its on-phone coach.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.ai import comfort
from app.ai.gemini import AiUnavailable
from app.core import daily_limits, ratelimit
from app.modules.auth.deps import CurrentStudent
from app.modules.chanakya import ai

router = APIRouter(prefix="/v1/chanakya", tags=["chanakya"])

DAY = 24 * 60 * 60
MIN_WORDS = 5


def _unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"message": "Chanakya is busy; using the built-in coach.", "code": "ai_unavailable"},
    )


class EnglishLineOut(BaseModel):
    text: str = Field(description="English, for the student to say")
    meaning: str | None = Field(description="Its meaning in the comfort language; null for English")


def _line(language: str, line: ai.EnglishLineShape | None) -> EnglishLineOut | None:
    if line is None:
        return None
    return EnglishLineOut(text=line.text, meaning=comfort.meaning(language, line.meaning))


# ---------- Chat ----------


class AnswersIn(BaseModel):
    topic: str | None = Field(None, max_length=300)
    date: str | None = Field(None, max_length=80)
    time: str | None = Field(None, max_length=80)
    audience: str | None = Field(None, max_length=200)
    fear: str | None = Field(None, max_length=300)


class HistoryIn(BaseModel):
    role: Literal["student", "chanakya"]
    text: str = Field(max_length=2000)


class ReplyIn(BaseModel):
    reason_id: str = Field(min_length=1, max_length=64)
    reason_label: str = Field(min_length=1, max_length=120)
    stage: ai.Stage
    answers: AnswersIn = Field(default_factory=AnswersIn)
    message: str = Field(min_length=1, max_length=2000)
    history: list[HistoryIn] = Field(default_factory=list, max_length=20)
    next_question: str | None = Field(
        None, max_length=300, description="The app's next follow-up question, asked in the comfort language"
    )


class ReplyOut(BaseModel):
    reply: str
    english_line: EnglishLineOut | None
    remaining: int = Field(description="Chanakya messages left today")


@router.post("/reply")
async def chat_reply(body: ReplyIn, student: CurrentStudent) -> ReplyOut:
    """One chat turn. Counts as one of today's 20 Chanakya messages (given back if the AI can't answer)."""
    if not body.message.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Type or say something first.", "code": "invalid_input"},
        )
    language = comfort.normalise(student.profile.comfort_language)
    ticket = await daily_limits.take(student.id, "chat_messages")
    try:
        result = await ai.reply(
            reason_label=body.reason_label,
            stage=body.stage,
            answers={key: value for key, value in body.answers.model_dump().items() if value},
            message=body.message,
            history=[(item.role, item.text) for item in body.history[-10:]],
            next_question=body.next_question,
            language=language,
            user_id=student.id,
        )
    except AiUnavailable as error:
        await daily_limits.give_back(ticket)
        raise _unavailable() from error
    return ReplyOut(
        reply=result.reply, english_line=_line(language, result.english_line), remaining=ticket.remaining
    )


# ---------- The "Before you record" card ----------


class BriefIn(BaseModel):
    reason_id: str = Field(min_length=1, max_length=64)
    reason_label: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=300)
    time_limit_sec: int = Field(ge=10, le=1800)
    level: ai.Level = "building"


class BriefOut(BaseModel):
    tips: list[str]
    structure: list[str]
    opening_line: EnglishLineOut
    warm_up: str


@router.post("/brief")
async def record_brief(body: BriefIn, student: CurrentStudent) -> BriefOut:
    """Doesn't count toward the daily limits (only a guard against runaway use)."""
    await ratelimit.hit(f"ai:chanakya-brief:{student.id}", limit=100, per_seconds=DAY)
    language = comfort.normalise(student.profile.comfort_language)
    try:
        result = await ai.brief(
            reason_label=body.reason_label,
            topic=body.topic,
            time_limit_sec=body.time_limit_sec,
            level=body.level,
            language=language,
            user_id=student.id,
        )
    except AiUnavailable as error:
        raise _unavailable() from error
    opening = _line(language, result.opening_line)
    assert opening is not None
    return BriefOut(
        tips=result.tips, structure=result.structure, opening_line=opening, warm_up=result.warm_up
    )


# ---------- Scoring a practice attempt ----------


class ContextIn(BaseModel):
    reason_id: str = Field(min_length=1, max_length=64)
    reason_label: str = Field(min_length=1, max_length=120)
    topic: str = Field("", max_length=300)
    time_limit_sec: int | None = Field(None, ge=0, le=1800)


class MetricsIn(BaseModel):
    """The phone's measures (src/features/speech/metrics.ts)."""

    duration_sec: float = Field(ge=0, le=3600)
    words_per_minute: float = Field(ge=0, le=1000)
    fillers_per_minute: float = Field(ge=0, le=1000)
    filler_words: dict[str, int] = Field(default_factory=dict, max_length=30)
    long_pauses: int = Field(0, ge=0, le=1000)
    volume_mean: float = Field(description="dBFS, e.g. -24")
    quiet_share: float = Field(ge=0, le=1)
    ending_strength: float | None = Field(None, ge=0, le=5)
    time_use: float | None = Field(None, ge=0, le=20)


class PhoneScoresIn(BaseModel):
    confidence: int = Field(ge=0, le=100)
    voice_tone: int = Field(ge=0, le=100)


class ScoreIn(BaseModel):
    transcript: str = Field(max_length=12000)
    context: ContextIn
    metrics: MetricsIn
    phone_scores: PhoneScoresIn


class GrammarOut(BaseModel):
    said: str
    better: str
    why: str


class BetterVersionOut(BaseModel):
    quote: str
    rewrite: str
    meaning: str | None


class ScoreOut(BaseModel):
    confidence: int
    voice_tone: int
    content: int
    overall: int
    tip: str
    best_line: str | None
    better_version: BetterVersionOut | None
    grammar: list[GrammarOut]
    remaining: int = Field(description="Scored attempts left today")


def _measures(metrics: MetricsIn) -> str:
    fillers = ", ".join(
        f"“{word[:30]}” ×{count}"
        for word, count in sorted(metrics.filler_words.items(), key=lambda item: -item[1])[:5]
    )
    lines = [
        f"- Length: {round(metrics.duration_sec)} s, {round(metrics.words_per_minute)} words per minute",
        f"- Fillers: {metrics.fillers_per_minute:.1f} per minute" + (f" ({fillers})" if fillers else ""),
        f"- Pauses longer than 3 seconds: {metrics.long_pauses}",
        f"- Volume: {metrics.volume_mean:.0f} dB average, "
        f"{round(metrics.quiet_share * 100)}% of the speech too quiet",
    ]
    if metrics.ending_strength is not None:
        lines.append(f"- Sentence endings: {round(metrics.ending_strength * 100)}% of the opening loudness")
    if metrics.time_use is not None:
        lines.append(f"- Time used: {round(metrics.time_use * 100)}% of the time limit")
    return "\n".join(lines)


@router.post("/score")
async def score_attempt(body: ScoreIn, student: CurrentStudent) -> ScoreOut:
    """The AI part of a practice score. Counts as one of today's 3 scored attempts (given back on 503)."""
    if len(body.transcript.split()) < MIN_WORDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "That was too short to score. Try speaking for a little longer.",
                "code": "too_short",
            },
        )
    language = comfort.normalise(student.profile.comfort_language)
    ticket = await daily_limits.take(student.id, "scored_attempts")
    try:
        result = await ai.score(
            transcript=body.transcript,
            reason_label=body.context.reason_label,
            topic=body.context.topic,
            time_limit_sec=body.context.time_limit_sec,
            measures=_measures(body.metrics),
            confidence=body.phone_scores.confidence,
            voice_tone=body.phone_scores.voice_tone,
            language=language,
            user_id=student.id,
        )
    except AiUnavailable as error:
        await daily_limits.give_back(ticket)
        raise _unavailable() from error
    confidence = max(0, min(100, body.phone_scores.confidence + result.confidence_adjust))
    voice_tone = max(0, min(100, body.phone_scores.voice_tone + result.voice_tone_adjust))
    better = result.better_version
    return ScoreOut(
        confidence=confidence,
        voice_tone=voice_tone,
        content=result.content,
        # The same equal weighting as the phone's own score (src/features/speech/score.ts).
        overall=round((confidence + voice_tone + result.content) / 3),
        tip=result.tip,
        best_line=result.best_line or None,
        better_version=(
            BetterVersionOut(
                quote=better.quote, rewrite=better.rewrite, meaning=comfort.meaning(language, better.meaning)
            )
            if better
            else None
        ),
        grammar=[GrammarOut(said=item.said, better=item.better, why=item.why) for item in result.grammar],
        remaining=ticket.remaining,
    )


# ---------- Today's allowance ----------


class AllowanceOut(BaseModel):
    used: int
    limit: int
    remaining: int


class UsageOut(BaseModel):
    day: str = Field(description="The India date the counts are for, YYYY-MM-DD")
    scored_attempts: AllowanceOut
    chat_messages: AllowanceOut


@router.get("/usage")
async def usage_today(student: CurrentStudent) -> UsageOut:
    used = await daily_limits.used_today(student.id)

    def allowance(kind: daily_limits.Kind) -> AllowanceOut:
        limit = daily_limits.LIMITS[kind]
        return AllowanceOut(used=used[kind], limit=limit, remaining=max(0, limit - used[kind]))

    return UsageOut(
        day=daily_limits.today().isoformat(),
        scored_attempts=allowance("scored_attempts"),
        chat_messages=allowance("chat_messages"),
    )
