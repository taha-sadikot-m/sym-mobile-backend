"""The signed-in student's own account: profile, username, password and account deletion."""

import secrets
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import ratelimit
from app.core.password_rules import problems
from app.models import (
    AccountDeletion,
    AiCall,
    Block,
    Challenge,
    ChallengeStatus,
    CodePurpose,
    ConsentStatus,
    EmailCode,
    LiveEvent,
    LiveResult,
    MunSession,
    Profile,
    PushToken,
    RateLimit,
    RefreshToken,
    UserReport,
)
from app.models.base import utcnow
from app.modules.auth import service
from app.modules.auth.deps import CurrentStudent, Session
from app.modules.auth.schemas import Experience, birth_year_from_age, clean_username
from app.modules.me import safety
from app.modules.me.ladder import Ladder, build_debate_skill_ladder
from app.web import debates as web_debates
from app.web import users
from app.web.debates import user_debate_stats

router = APIRouter(prefix="/v1", tags=["student account"])


class MeOut(BaseModel):
    id: int
    email: str
    full_name: str
    username: str | None
    age: int | None
    institution: str
    city: str
    country: str
    mun_experience: str | None
    email_verified: bool
    member_since: datetime
    rank: int
    rank_tier: str
    consent_status: str
    parent_email: str | None


class MePatch(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=120)
    username: str | None = None
    age: int | None = Field(None, ge=8, le=100)
    institution: str | None = Field(None, max_length=160)
    city: str | None = Field(None, max_length=80)
    country: str | None = Field(None, max_length=80)
    mun_experience: Experience | None = None

    _username = field_validator("username")(clean_username)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _rules(self) -> "PasswordChangeIn":
        issue = problems(self.new_password)
        if issue:
            raise ValueError(issue)
        return self


class DeleteIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class UsernameOut(BaseModel):
    username: str
    available: bool


def _me(student_user: users.WebUser, profile: Profile) -> MeOut:
    return MeOut(
        id=student_user.id,
        email=student_user.email,
        full_name=student_user.full_name,
        username=profile.username,
        age=date.today().year - profile.birth_year if profile.birth_year else None,
        institution=profile.institution,
        city=profile.city,
        country=profile.country,
        mun_experience=profile.mun_experience,
        email_verified=student_user.is_email_verified,
        member_since=student_user.created_at,
        rank=student_user.current_rank,
        rank_tier=student_user.rank_tier,
        consent_status=consent_status(profile),
        parent_email=profile.parent_email,
    )


def consent_status(profile: Profile) -> str:
    """The app's view: 'confirmed', 'not_needed' (18+ or age unknown), or 'pending'."""
    if profile.consent_status == ConsentStatus.GRANTED:
        return "confirmed"
    return "pending" if safety.needs_consent(profile) else "not_needed"


class ConsentIn(BaseModel):
    parent_email: EmailStr


class ConsentCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


@router.post("/me/consent")
async def request_consent(body: ConsentIn, student: CurrentStudent, session: Session) -> MeOut:
    """Email a code to a parent or guardian; the student enters it once the parent agrees."""
    await ratelimit.hit(f"consent:{student.id}", limit=5, per_seconds=60 * 60)
    await ratelimit.hit(f"consent-day:{student.id}", limit=10, per_seconds=24 * 60 * 60)
    parent = str(body.parent_email).strip().lower()
    if parent == student.user.email.strip().lower():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Use your parent's or guardian's email, not your own.",
                "code": "invalid_input",
            },
        )
    student.profile.parent_email = parent
    if student.profile.consent_status != ConsentStatus.GRANTED:
        student.profile.consent_status = ConsentStatus.PENDING
    await service.send_code(session, student.user, CodePurpose.PARENT_CONSENT, to=parent)
    await session.commit()
    return _me(student.user, student.profile)


@router.post("/me/consent/confirm")
async def confirm_consent(body: ConsentCodeIn, student: CurrentStudent, session: Session) -> MeOut:
    await ratelimit.hit(f"consent-code:{student.id}", limit=10, per_seconds=15 * 60)
    try:
        await service.use_code(session, student.user, CodePurpose.PARENT_CONSENT, body.code)
    except service.AuthError as error:
        raise HTTPException(error.status, detail={"message": str(error), "code": error.code}) from error
    student.profile.consent_status = ConsentStatus.GRANTED
    student.profile.consent_at = utcnow()
    await session.commit()
    return _me(student.user, student.profile)


@router.get("/me")
async def me(student: CurrentStudent) -> MeOut:
    return _me(student.user, student.profile)


class LadderOut(BaseModel):
    ladder: Ladder
    total_debates: int
    wins: int
    losses: int
    draws: int
    win_streak: int
    longest_win_streak: int


@router.get("/me/ladder")
async def my_ladder(student: CurrentStudent, session: Session) -> LadderOut:
    """The website's Skill Ladder for this student, from the same stats row the website reads."""
    row = (
        await session.execute(select(user_debate_stats).where(user_debate_stats.c.user_id == student.id))
    ).first()
    stats = dict(row._mapping) if row else {}
    return LadderOut(
        ladder=build_debate_skill_ladder(stats),
        total_debates=int(stats.get("total_debates") or 0),
        wins=int(stats.get("total_wins") or 0),
        losses=int(stats.get("total_losses") or 0),
        draws=int(stats.get("total_draws") or 0),
        win_streak=int(stats.get("current_win_streak") or 0),
        longest_win_streak=int(stats.get("longest_win_streak") or 0),
    )


class PushTokenIn(BaseModel):
    token: str = Field(pattern=r"^Expo(nent)?PushToken\[[A-Za-z0-9_\-]{10,160}\]$", max_length=200)
    platform: Literal["ios", "android"]


@router.put("/me/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def save_push_token(body: PushTokenIn, student: CurrentStudent, session: Session) -> None:
    """Register this device for notifications (a device signed in to a new account moves to it)."""
    await ratelimit.hit(f"push-token:{student.id}", limit=60, per_seconds=24 * 60 * 60)
    stmt = pg_insert(PushToken).values(token=body.token, user_id=student.id, platform=body.platform)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[PushToken.token],
            set_={"user_id": student.id, "platform": body.platform, "last_seen_at": utcnow()},
        )
    )
    await session.commit()


class PushTokenOut(BaseModel):
    token: str = Field(max_length=200)


@router.delete("/me/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def forget_push_token(body: PushTokenOut, student: CurrentStudent, session: Session) -> None:
    """Stop notifications on this device (sign-out, or the student turned them off)."""
    await session.execute(
        delete(PushToken).where(PushToken.token == body.token, PushToken.user_id == student.id)
    )
    await session.commit()


@router.patch("/me")
async def update_me(body: MePatch, student: CurrentStudent, session: Session) -> MeOut:
    values = body.model_dump(exclude_unset=True)
    profile = student.profile
    if "username" in values and values["username"] != profile.username:
        if values["username"] and await service.username_taken(
            session, values["username"], except_user=student.id
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"message": "That username is taken.", "code": "username_taken"},
            )
        profile.username = values["username"]
    if "age" in values:
        profile.birth_year = birth_year_from_age(values["age"])
    for field in ("institution", "city", "country"):
        if field in values and values[field] is not None:
            setattr(profile, field, values[field].strip())
    if values.get("mun_experience"):
        profile.mun_experience = values["mun_experience"]
    if values.get("full_name"):
        # The name lives on the shared account, so it changes on the website too.
        await users.set_fields(session, student.id, full_name=values["full_name"].strip())
    await session.commit()
    user = await users.by_id(session, student.id)
    assert user is not None
    return _me(user, profile)


@router.get("/usernames/{username}")
async def check_username(username: str, student: CurrentStudent, session: Session) -> UsernameOut:
    try:
        cleaned = clean_username(username)
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"message": str(error), "code": "invalid_username"}
        ) from error
    if cleaned is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Enter a username.", "code": "invalid_username"},
        )
    taken = await service.username_taken(session, cleaned, except_user=student.id)
    return UsernameOut(username=cleaned, available=not taken)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordChangeIn, student: CurrentStudent, session: Session) -> None:
    """Changes the password everywhere (website too) and signs out other devices."""
    await ratelimit.hit(f"password:user:{student.id}", limit=10, per_seconds=15 * 60)
    if not await service.password_matches(body.current_password, student.user.password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"message": "Your current password isn’t right.", "code": "password_wrong"},
        )
    await users.set_fields(session, student.id, password=await service.hash_password(body.new_password))
    await service.revoke_all(session, student.id)
    await session.commit()


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(body: DeleteIn, request: Request, student: CurrentStudent, session: Session) -> None:
    """Delete the account. Because it is shared, this also removes it from the website.

    The `users` row is kept (debate history on the website points to it) but emptied of personal data and
    made unusable; everything the app stored about the student is deleted.
    """
    await ratelimit.hit(f"delete:user:{student.id}", limit=5, per_seconds=15 * 60)
    if not await service.password_matches(body.password, student.user.password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"message": "That password isn’t right.", "code": "password_wrong"},
        )
    await erase_student(session, student.id, requested_by="student")
    await session.commit()


async def erase_student(session: Session, user_id: int, *, requested_by: str) -> None:
    """Remove personal data for a student (shared with the admin console's delete action).

    Deleted: profile, sign-in tokens and codes, MUN sessions, their spoken turns in live debates, their
    private live reports, blocks, reports they filed, rate-limit counters keyed by their email. Anonymised:
    the shared `users` row (kept because website history points to it), their messages in the website's
    debate transcripts (emptied with UPDATE — this backend never deletes website rows), AI usage rows.
    Reports *about* them are kept for the moderation record.
    """
    old_email = await session.scalar(select(users.users.c.email).where(users.users.c.id == user_id))
    uid = str(user_id)
    await session.execute(delete(MunSession).where(MunSession.user_id == user_id))
    await session.execute(delete(PushToken).where(PushToken.user_id == user_id))
    await session.execute(delete(LiveEvent).where(LiveEvent.user_id == user_id, LiveEvent.kind == "turn"))
    await session.execute(
        update(LiveResult)
        .where(LiveResult.reports.has_key(uid))
        .values(reports=LiveResult.reports.op("-")(uid))
    )
    await session.execute(
        update(Challenge)
        .where(
            or_(Challenge.challenger_id == user_id, Challenge.opponent_id == user_id),
            Challenge.status.in_(
                [
                    ChallengeStatus.PENDING,
                    ChallengeStatus.ACCEPTED,
                    ChallengeStatus.COUNTERED,
                    ChallengeStatus.LIVE,
                ]
            ),
        )
        .values(status=ChallengeStatus.CANCELLED)
    )
    await session.execute(delete(Block).where(or_(Block.blocker_id == user_id, Block.blocked_id == user_id)))
    await session.execute(delete(UserReport).where(UserReport.reporter_id == user_id))
    await session.execute(update(AiCall).where(AiCall.user_id == user_id).values(user_id=None))
    await session.execute(
        update(web_debates.debate_messages)
        .where(web_debates.debate_messages.c.sender_id == user_id)
        .values(content="[deleted]")
    )
    if old_email:
        await session.execute(delete(RateLimit).where(RateLimit.key.endswith(f":{old_email.lower()}")))
    await users.set_fields(
        session,
        user_id,
        email=f"deleted-{user_id}-{secrets.token_hex(4)}@deleted.invalid",
        full_name="Deleted user",
        password="!" + secrets.token_urlsafe(30),  # Django treats "!…" as an unusable password
        is_active=False,
        is_email_verified=False,
    )
    for model in (RefreshToken, EmailCode):
        await session.execute(delete(model).where(model.user_id == user_id))
    await session.execute(delete(Profile).where(Profile.user_id == user_id))
    session.add(AccountDeletion(user_id=user_id, requested_by=requested_by))
