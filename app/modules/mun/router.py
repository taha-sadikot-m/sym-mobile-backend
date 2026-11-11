"""MUN single player for the app: AI opponents, transcription, speech analysis, POIs, feedback, the
phone's report explained in the comfort language, and history.

Every AI endpoint answers 503 with code "ai_unavailable" when the AI can't help (no key, outage, odd
answer); the app then uses its built-in coach for that step, so a session never gets stuck.
"""

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.ai import comfort, deepgram
from app.ai.gemini import AiUnavailable
from app.core import ratelimit
from app.models import Agenda, Committee, CommitteeSeat, ContentStatus, Country, SeatRule
from app.models.mun import MunSession
from app.modules.auth.deps import CurrentStudent, Session
from app.modules.mun import ai

router = APIRouter(prefix="/v1/mun", tags=["mun"])

DAY = 24 * 60 * 60
MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_TYPES = {
    "audio/m4a",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/ogg",
    "audio/3gpp",
    "video/mp4",
    "application/octet-stream",
}


def _unavailable(error: Exception) -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "message": "The AI coach is busy right now; using the built-in coach.",
            "code": "ai_unavailable",
        },
    )


async def _limit(student_id: int, feature: str, per_day: int) -> None:
    await ratelimit.hit(f"ai:{feature}:{student_id}", limit=per_day, per_seconds=DAY)


class Context(BaseModel):
    committee: str = Field(
        min_length=2, max_length=160, description="Committee name, e.g. 'UN Security Council'"
    )
    committee_slug: str | None = Field(None, max_length=64)
    agenda: str = Field(min_length=3, max_length=300)
    country: str = Field(min_length=2, max_length=120, description="The student's delegation")


# ---------- Opponents ----------


class OpponentsIn(Context):
    country_code: str | None = Field(None, min_length=2, max_length=2)


class OpponentOut(BaseModel):
    country_code: str
    country_name: str
    stance: ai.Stance
    interests: list[str]
    position: str
    personality: str
    context: str


async def _candidates(session: Session, slug: str | None, exclude: str | None) -> dict[str, str]:
    committee = await session.scalar(select(Committee).where(Committee.slug == slug)) if slug else None
    stmt = select(Country.code, Country.name).where(Country.is_active)
    if committee is not None and committee.seat_rule == SeatRule.SELECTED:
        stmt = stmt.join(CommitteeSeat, CommitteeSeat.country_code == Country.code).where(
            CommitteeSeat.committee_id == committee.id
        )
    else:
        stmt = stmt.where(Country.is_un_member)
    rows = {code: name for code, name in await session.execute(stmt)}
    rows.pop((exclude or "").lower(), None)
    return rows


@router.post("/opponents")
async def opponents(body: OpponentsIn, student: CurrentStudent, session: Session) -> list[OpponentOut]:
    await _limit(student.id, "opponents", 60)
    candidates = await _candidates(session, body.committee_slug, body.country_code)
    if len(candidates) < 3:
        raise _unavailable(AiUnavailable("too few countries"))
    try:
        picks = await ai.pick_opponents(
            committee=body.committee,
            agenda=body.agenda,
            country=body.country,
            candidates=candidates,
            user_id=student.id,
        )
    except AiUnavailable as error:
        raise _unavailable(error) from error
    return [OpponentOut(country_name=candidates[pick.country_code], **pick.model_dump()) for pick in picks]


# ---------- Transcription ----------


class TranscriptOut(BaseModel):
    text: str
    seconds: float


FEATURES = {"speech": "mun.speech", "poi_answer": "mun.poi_answer", "debate_turn": "debate.turn"}


async def transcribe_upload(file: UploadFile, student_id: int, purpose: str) -> TranscriptOut:
    """Speech to text. The audio is passed to the transcription service and then discarded — never stored."""
    await _limit(student_id, "transcribe", 300)
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"message": "That recording is too long.", "code": "too_large"},
        )
    if len(audio) < 1000:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "That recording is empty. Try again.", "code": "empty_audio"},
        )
    content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if content_type not in AUDIO_TYPES:
        content_type = "application/octet-stream"
    try:
        text, seconds = await deepgram.transcribe(
            audio, content_type, feature=FEATURES[purpose], user_id=student_id
        )
    except AiUnavailable as error:
        raise _unavailable(error) from error
    return TranscriptOut(text=text, seconds=seconds)


@router.post("/transcribe")
async def transcribe(
    file: UploadFile,
    student: CurrentStudent,
    purpose: Annotated[Literal["speech", "poi_answer", "debate_turn"], Form()] = "speech",
) -> TranscriptOut:
    return await transcribe_upload(file, student.id, purpose)


# ---------- Speech analysis ----------


class SpeechIn(Context):
    text: str = Field(min_length=1, max_length=20000)


class ClaimOut(BaseModel):
    text: str
    supported: bool


class SpeechOut(BaseModel):
    arguments: list[str]
    proposals: list[str]
    claims: list[ClaimOut]
    mentioned_countries: list[str]


@router.post("/speech-analysis")
async def speech_analysis(body: SpeechIn, student: CurrentStudent) -> SpeechOut:
    await _limit(student.id, "speech", 60)
    try:
        result = await ai.analyse_speech(
            committee=body.committee,
            agenda=body.agenda,
            country=body.country,
            text=body.text,
            user_id=student.id,
        )
    except AiUnavailable as error:
        raise _unavailable(error) from error
    return SpeechOut.model_validate(result.model_dump())


# ---------- Points of Information ----------


class OpponentRef(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=120)
    stance: ai.Stance
    position: str = Field("", max_length=600)


class PoisIn(SpeechIn):
    analysis: SpeechOut
    opponents: list[OpponentRef] = Field(min_length=1, max_length=6)


class PoiOut(BaseModel):
    country_code: str
    based_on: str
    question: str


@router.post("/pois")
async def pois(body: PoisIn, student: CurrentStudent) -> list[PoiOut]:
    await _limit(student.id, "pois", 60)
    try:
        points = await ai.write_pois(
            committee=body.committee,
            agenda=body.agenda,
            country=body.country,
            text=body.text,
            analysis=ai.SpeechShape.model_validate(body.analysis.model_dump()),
            opponents=[item.model_dump() for item in body.opponents],
            user_id=student.id,
        )
    except AiUnavailable as error:
        raise _unavailable(error) from error
    return [PoiOut.model_validate(point.model_dump()) for point in points]


# ---------- Feedback ----------


class PointIn(BaseModel):
    country: str = Field(max_length=120)
    question: str = Field(max_length=1200)
    answer: str | None = Field(None, max_length=8000)


class FeedbackIn(SpeechIn):
    duration_ms: int = Field(ge=0, le=20 * 60 * 1000)
    points: list[PointIn] = Field(default_factory=list, max_length=6)


class CategoryOut(BaseModel):
    id: ai.CategoryId
    label: str
    score: int
    strengths: list[str]
    improvements: list[str]
    evidence: list[str]


class FeedbackOut(BaseModel):
    overall_score: int
    categories: list[CategoryOut]
    supported_claims: list[str]
    questionable_claims: list[str]
    references: list[str]
    improvement_actions: list[str]
    language: comfort.ComfortLanguage = Field(
        "en", description="The comfort language strengths, improvements and practice steps are written in"
    )


LABELS = {
    "speechClarity": "Speech Clarity",
    "foreignPolicy": "Foreign Policy",
    "research": "Research",
    "poiPerformance": "POI Performance",
}
ORDER = list(LABELS)


async def _frameworks(session: Session, slug: str | None, agenda: str) -> list[str]:
    if not slug:
        return []
    frameworks = await session.scalar(
        select(Agenda.key_frameworks)
        .join(Committee, Committee.id == Agenda.committee_id)
        .where(Committee.slug == slug, Agenda.title == agenda, Agenda.status == ContentStatus.PUBLISHED)
    )
    return list(frameworks or [])


@router.post("/feedback")
async def feedback(body: FeedbackIn, student: CurrentStudent, session: Session) -> FeedbackOut:
    await _limit(student.id, "feedback", 40)
    language = comfort.normalise(student.profile.comfort_language)
    try:
        result = await ai.write_feedback(
            committee=body.committee,
            agenda=body.agenda,
            country=body.country,
            text=body.text,
            duration_ms=body.duration_ms,
            pois=[point.model_dump() for point in body.points],
            frameworks=await _frameworks(session, body.committee_slug, body.agenda),
            user_id=student.id,
            language=language,
        )
    except AiUnavailable as error:
        raise _unavailable(error) from error
    categories = sorted(result.categories, key=lambda item: ORDER.index(item.id))
    return FeedbackOut(
        # Computed here, not by the model, so every report adds up the same way.
        overall_score=round(sum(item.score for item in categories) / len(categories)),
        categories=[CategoryOut(label=LABELS[item.id], **item.model_dump()) for item in categories],
        supported_claims=result.supported_claims[:6],
        questionable_claims=result.questionable_claims[:6],
        references=result.references,
        improvement_actions=result.improvement_actions,
        language=language,
    )


# ---------- The phone's report in the comfort language ----------

ExplainId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[\w.:-]+$")]


class ExplainItemIn(BaseModel):
    id: ExplainId
    text: str = Field(max_length=600, description="A coaching note from the phone's report, in English")


class ExplainPhraseIn(BaseModel):
    id: ExplainId
    quote: str = Field(max_length=600, description="What the student said")
    rewrite: str = Field(max_length=600, description="The better English version (returned unchanged)")


class ExplainIn(BaseModel):
    items: list[ExplainItemIn] = Field(default_factory=list, max_length=40)
    phrases: list[ExplainPhraseIn] = Field(default_factory=list, max_length=20)

    @field_validator("items", "phrases")
    @classmethod
    def _unique(cls, value: list[ExplainItemIn] | list[ExplainPhraseIn]) -> Any:
        if len({item.id for item in value}) != len(value):
            raise ValueError("Ids must be unique.")
        return value


class ExplainedItemOut(BaseModel):
    id: str
    text: str = Field(description="In the comfort language (unchanged for English)")


class PhraseMeaningOut(BaseModel):
    id: str
    meaning: str | None = Field(description="The rewrite's meaning in the comfort language; null for English")


class ExplainOut(BaseModel):
    items: list[ExplainedItemOut]
    phrases: list[PhraseMeaningOut]
    language: comfort.ComfortLanguage


@router.post("/explain")
async def explain_report(body: ExplainIn, student: CurrentStudent) -> ExplainOut:
    """The phone builds the MUN report itself; this puts its coaching notes in the student's comfort language
    and gives the meaning of each better phrase (which stays English). English students get their text back
    with no AI call. Doesn't count toward the daily limits (only a guard against runaway use).
    """
    await _limit(student.id, "explain", 100)
    language = comfort.normalise(student.profile.comfort_language)
    items = [(item.id, item.text) for item in body.items if item.text.strip()]
    phrases = [(phrase.id, phrase.quote, phrase.rewrite) for phrase in body.phrases if phrase.rewrite.strip()]
    texts: dict[str, str] = {}
    meanings: dict[str, str | None] = {}
    if language != "en" and (items or phrases):
        try:
            texts, meanings = await ai.explain(
                items=items, phrases=phrases, language=language, user_id=student.id
            )
        except AiUnavailable as error:
            raise _unavailable(error) from error
    return ExplainOut(
        items=[ExplainedItemOut(id=item.id, text=texts.get(item.id, item.text)) for item in body.items],
        phrases=[PhraseMeaningOut(id=phrase.id, meaning=meanings.get(phrase.id)) for phrase in body.phrases],
        language=language,
    )


# ---------- History ----------


class SessionIn(BaseModel):
    run_id: uuid.UUID
    mode: Literal["single", "multi"] = "single"
    committee: str = Field(max_length=160)
    committee_slug: str | None = Field(None, max_length=64)
    agenda: str = Field(max_length=300)
    country: str = Field(max_length=120)
    overall_score: int = Field(ge=0, le=100)
    scores: dict[str, int] = Field(default_factory=dict)
    speech_seconds: int = Field(0, ge=0, le=3600)
    pois_answered: int = Field(0, ge=0, le=10)
    ai_feedback: bool = False
    report: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime | None = None

    @field_validator("report")
    @classmethod
    def _small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > 60_000:
            raise ValueError("That report is too large.")
        return value

    @field_validator("scores")
    @classmethod
    def _few(cls, value: dict[str, int]) -> dict[str, int]:
        if len(value) > 12 or any(len(key) > 32 for key in value):
            raise ValueError("Too many scores.")
        return value


class SessionOut(SessionIn):
    pass


@router.put("/sessions/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def save_session(run_id: uuid.UUID, body: SessionIn, student: CurrentStudent, session: Session) -> None:
    """Save a finished session (idempotent: saving the same run again replaces it)."""
    if body.run_id != run_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Run id mismatch.", "code": "invalid_input"},
        )
    owner = await session.scalar(select(MunSession.user_id).where(MunSession.id == run_id))
    if owner is not None and owner != student.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "That session belongs to someone else.", "code": "conflict"},
        )
    values = body.model_dump(exclude={"run_id", "completed_at"}) | {
        "id": run_id,
        "user_id": student.id,
        "scores": {key: max(0, min(100, int(value))) for key, value in body.scores.items()},
    }
    if body.completed_at:
        values["completed_at"] = body.completed_at
    stmt = insert(MunSession).values(**values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[MunSession.id],
            set_={key: stmt.excluded[key] for key in values if key not in ("id", "user_id")},
            # Never overwrite another student's session, even if two requests race on the same id.
            where=MunSession.user_id == student.id,
        )
    )
    await session.commit()


@router.get("/sessions")
async def list_sessions(
    student: CurrentStudent, session: Session, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[SessionOut]:
    rows = await session.scalars(
        select(MunSession)
        .where(MunSession.user_id == student.id)
        .order_by(MunSession.completed_at.desc())
        .limit(limit)
    )
    return [
        SessionOut(
            run_id=row.id,
            mode=row.mode,
            committee=row.committee,
            committee_slug=row.committee_slug,
            agenda=row.agenda,
            country=row.country,
            overall_score=row.overall_score,
            scores=row.scores,
            speech_seconds=row.speech_seconds,
            pois_answered=row.pois_answered,
            ai_feedback=row.ai_feedback,
            report=row.report,
            completed_at=row.completed_at,
        )
        for row in rows
    ]
