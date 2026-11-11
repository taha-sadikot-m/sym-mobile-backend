"""Student accounts: sign-up, email codes, sign-in, token refresh and sign-out.

Accounts live in the web's `users` table, so a student can use the same email and password on the website
and in the app. Hashing uses Django's format with 1,000,000 rounds; it runs in a worker thread so one
sign-in never stalls other requests.
"""

import asyncio
import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import email as mail
from app.core import tokens
from app.core.config import Settings, get_settings
from app.core.passwords import check_django_password, make_django_password
from app.models import AppStatus, CodePurpose, EmailCode, Profile, RefreshToken
from app.models.base import utcnow
from app.web import users
from app.web.users import APP_USER_TYPES, WebUser

CODE_MINUTES = 15
CODE_ATTEMPTS = 5

# Checked when the email is unknown, so response time doesn't reveal which emails have accounts.
_DUMMY_HASH = make_django_password("not-a-real-password", iterations=get_settings().password_hash_iterations)


class AuthError(Exception):
    """Shown to the student as-is. `code` lets the app react (e.g. open the verify-email screen)."""

    def __init__(self, message: str, *, code: str = "invalid", status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: int


async def hash_password(password: str) -> str:
    iterations = get_settings().password_hash_iterations
    return await asyncio.to_thread(make_django_password, password, iterations=iterations)


async def password_matches(password: str, encoded: str) -> bool:
    return await asyncio.to_thread(check_django_password, password, encoded)


# ---------- Email codes ----------


async def send_code(
    session: AsyncSession, user: WebUser, purpose: CodePurpose, *, to: str | None = None
) -> None:
    """Replace any earlier code for this purpose and email a new one (the caller commits).

    `to` sends it somewhere else (a parent's address for consent).
    """
    await session.execute(
        delete(EmailCode).where(
            EmailCode.user_id == user.id, EmailCode.purpose == purpose, EmailCode.used_at.is_(None)
        )
    )
    code = tokens.new_code()
    session.add(
        EmailCode(
            user_id=user.id,
            purpose=purpose,
            code_hash=tokens.hash_token(code),
            expires_at=utcnow() + timedelta(minutes=CODE_MINUTES),
        )
    )
    await session.flush()
    # Sent after the response is ready would be nicer, but a code that never arrives is worse: send now.
    if purpose == CodePurpose.PARENT_CONSENT:
        await mail.send(mail.consent_email(to=to or user.email, student=user.full_name, code=code))
    else:
        await mail.send(mail.code_email(to=user.email, name=user.full_name, code=code, purpose=purpose))


async def use_code(session: AsyncSession, user: WebUser, purpose: CodePurpose, code: str) -> None:
    row = await session.scalar(
        select(EmailCode)
        .where(
            EmailCode.user_id == user.id,
            EmailCode.purpose == purpose,
            EmailCode.used_at.is_(None),
            EmailCode.expires_at > utcnow(),
        )
        .order_by(EmailCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if row is None:
        raise AuthError("That code has expired. Ask for a new one.", code="code_expired", status=400)
    row.attempts += 1
    if row.attempts > CODE_ATTEMPTS:
        row.used_at = utcnow()
        await session.commit()
        raise AuthError("Too many wrong codes. Ask for a new one.", code="code_expired", status=400)
    if not hmac.compare_digest(row.code_hash, tokens.hash_token(code.strip())):
        await session.commit()
        raise AuthError(
            "That code isn’t right. Check the email and try again.", code="code_wrong", status=400
        )
    row.used_at = utcnow()


# ---------- Tokens ----------


async def issue_tokens(
    session: AsyncSession,
    settings: Settings,
    user_id: int,
    *,
    family_id: uuid.UUID | None = None,
    device: str | None = None,
) -> TokenPair:
    family = family_id or uuid.uuid4()
    refresh = tokens.new_refresh_token()
    session.add(
        RefreshToken(
            token_hash=tokens.hash_token(refresh),
            user_id=user_id,
            family_id=family,
            expires_at=utcnow() + timedelta(days=settings.refresh_token_days),
            device=(device or "")[:160] or None,
        )
    )
    access, lifetime = tokens.make_access_token(settings, user_id=user_id, family_id=family)
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=lifetime, user_id=user_id)


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


# ---------- Who may use the app ----------


def check_allowed(user: WebUser, profile: Profile | None) -> None:
    if user.user_type not in APP_USER_TYPES:
        raise AuthError(
            "This account type can only sign in on the website.", code="wrong_account_type", status=403
        )
    if not user.is_email_verified:
        raise AuthError("Confirm your email to continue.", code="email_unverified", status=403)
    if not user.is_active:
        raise AuthError(
            "This account is switched off. Contact SYM support.", code="account_inactive", status=403
        )
    if profile is not None and profile.app_status == AppStatus.SUSPENDED:
        raise AuthError(
            "Your access to the app is paused. Contact SYM support.", code="app_suspended", status=403
        )


async def ensure_profile(session: AsyncSession, user_id: int) -> Profile:
    """Website accounts get an app profile the first time they sign in to the app."""
    profile = await session.get(Profile, user_id)
    if profile is None:
        profile = Profile(user_id=user_id)
        session.add(profile)
        await session.flush()
    return profile


# ---------- Flows ----------


async def sign_up(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    username: str | None,
    birth_year: int | None,
    institution: str,
    city: str,
    country: str,
    mun_experience: str | None = None,
    comfort_language: str = "en",
) -> WebUser:
    if await users.email_taken(session, email):
        raise AuthError(
            "An account with this email already exists — on the website or the app. Sign in instead.",
            code="email_taken",
            status=409,
        )
    if username and await username_taken(session, username):
        raise AuthError("That username is taken.", code="username_taken", status=409)
    user_id = await users.create(
        session, email=email, full_name=full_name, password_hash=await hash_password(password)
    )
    session.add(
        Profile(
            user_id=user_id,
            username=username,
            birth_year=birth_year,
            institution=institution.strip(),
            city=city.strip(),
            country=country.strip(),
            mun_experience=mun_experience,
            comfort_language=comfort_language,
        )
    )
    user = await users.by_id(session, user_id)
    assert user is not None
    await send_code(session, user, CodePurpose.VERIFY_EMAIL)
    await session.commit()
    return user


async def sign_in(
    session: AsyncSession, settings: Settings, *, email: str, password: str, device: str | None
) -> TokenPair:
    user = await users.by_email(session, email)
    if user is None:
        await password_matches(password, _DUMMY_HASH)
        raise AuthError("Email or password is incorrect.")
    if not await password_matches(password, user.password):
        raise AuthError("Email or password is incorrect.")
    profile = await session.get(Profile, user.id)
    if not user.is_email_verified and user.user_type in APP_USER_TYPES:
        await send_code(session, user, CodePurpose.VERIFY_EMAIL)
        await session.commit()
    check_allowed(user, profile)
    await ensure_profile(session, user.id)
    await users.record_login(session, user.id)
    pair = await issue_tokens(session, settings, user.id, device=device)
    await session.commit()
    return pair


async def verify_email(
    session: AsyncSession, settings: Settings, *, email: str, code: str, device: str | None
) -> TokenPair:
    user = await users.by_email(session, email)
    if user is None:
        raise AuthError(
            "That code isn’t right. Check the email and try again.", code="code_wrong", status=400
        )
    if user.is_email_verified:
        # Never issue tokens without a valid code: a verified account signs in with its password.
        raise AuthError(
            "This email is already verified. Sign in with your password.", code="already_verified", status=409
        )
    await use_code(session, user, CodePurpose.VERIFY_EMAIL, code)
    await users.mark_verified(session, user.id)
    user = await users.by_id(session, user.id)
    assert user is not None
    check_allowed(user, await session.get(Profile, user.id))
    await ensure_profile(session, user.id)
    await users.record_login(session, user.id)
    pair = await issue_tokens(session, settings, user.id, device=device)
    await session.commit()
    return pair


async def resend_verification(session: AsyncSession, *, email: str) -> None:
    user = await users.by_email(session, email)
    if user is not None and not user.is_email_verified and user.user_type in APP_USER_TYPES:
        await send_code(session, user, CodePurpose.VERIFY_EMAIL)
        await session.commit()


async def forgot_password(session: AsyncSession, *, email: str) -> None:
    user = await users.by_email(session, email)
    # A verified account that was switched off (e.g. banned on the website) can't reopen itself by resetting.
    switched_off = user is not None and user.is_email_verified and not user.is_active
    if user is not None and user.user_type in APP_USER_TYPES and not switched_off:
        await send_code(session, user, CodePurpose.RESET_PASSWORD)
        await session.commit()


async def reset_password(
    session: AsyncSession, settings: Settings, *, email: str, code: str, new_password: str, device: str | None
) -> TokenPair:
    user = await users.by_email(session, email)
    if user is None:
        raise AuthError(
            "That code isn’t right. Check the email and try again.", code="code_wrong", status=400
        )
    if user.is_email_verified and not user.is_active:
        raise AuthError(
            "This account is switched off. Contact SYM support.", code="account_inactive", status=403
        )
    await use_code(session, user, CodePurpose.RESET_PASSWORD, code)
    # The code proves the student owns the inbox, so a never-verified account is verified (and activated).
    await users.set_fields(
        session, user.id, password=await hash_password(new_password), is_email_verified=True, is_active=True
    )
    await revoke_all(session, user.id)
    refreshed = await users.by_id(session, user.id)
    assert refreshed is not None
    check_allowed(refreshed, await session.get(Profile, user.id))
    await ensure_profile(session, user.id)
    pair = await issue_tokens(session, settings, user.id, device=device)
    await session.commit()
    return pair


async def refresh(
    session: AsyncSession, settings: Settings, *, refresh_token: str, device: str | None
) -> TokenPair:
    # Locked, so two refreshes with the same token can't both succeed.
    row = await session.get(RefreshToken, tokens.hash_token(refresh_token), with_for_update=True)
    if row is None:
        raise AuthError("Please sign in again.", code="session_expired")
    if row.revoked_at is not None or row.used_at is not None:
        # An old token came back: someone may have copied it. Sign that device out everywhere it's used.
        await revoke_family(session, row.family_id)
        await session.commit()
        raise AuthError("Please sign in again.", code="session_expired")
    if row.expires_at <= utcnow():
        raise AuthError("Please sign in again.", code="session_expired")
    user = await users.by_id(session, row.user_id)
    if user is None:
        raise AuthError("Please sign in again.", code="session_expired")
    check_allowed(user, await session.get(Profile, user.id))
    row.used_at = utcnow()
    pair = await issue_tokens(
        session, settings, user.id, family_id=row.family_id, device=device or row.device
    )
    await session.commit()
    return pair


async def sign_out(session: AsyncSession, *, refresh_token: str) -> None:
    row = await session.get(RefreshToken, tokens.hash_token(refresh_token))
    if row is not None:
        await revoke_family(session, row.family_id)
        await session.commit()


async def username_taken(session: AsyncSession, username: str, *, except_user: int | None = None) -> bool:
    stmt = select(Profile.user_id).where(func.lower(Profile.username) == username.lower())
    if except_user is not None:
        stmt = stmt.where(Profile.user_id != except_user)
    return await session.scalar(stmt) is not None
