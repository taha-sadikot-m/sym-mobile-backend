"""Admin sign-in: email + password, then a mandatory authenticator-app code, then a session cookie."""

import asyncio
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, signing, totp
from app.core.config import Settings
from app.core.passwords import burn_admin_check, check_admin_password
from app.models import AdminSession, AdminUser
from app.models.base import utcnow

CHALLENGE_PURPOSE = "admin-2fa"
CHALLENGE_TTL_SECONDS = 300


class SignInError(Exception):
    """Shown to the user as-is, so it never says whether the email exists."""


@dataclass(frozen=True)
class PasswordStep:
    next: Literal["totp", "totp_setup"]
    challenge: str
    # Only for first sign-in, to set up the authenticator app.
    secret: str | None = None
    qr_svg: str | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _locked(admin: AdminUser) -> bool:
    return admin.locked_until is not None and admin.locked_until > utcnow()


def _register_failure(admin: AdminUser, settings: Settings) -> None:
    admin.failed_logins += 1
    if admin.failed_logins >= settings.admin_max_failed_logins:
        admin.locked_until = utcnow() + timedelta(minutes=settings.admin_lockout_minutes)
        admin.failed_logins = 0


async def check_password(
    session: AsyncSession, settings: Settings, *, email: str, password: str, ip: str | None
) -> PasswordStep:
    admin = await session.scalar(select(AdminUser).where(AdminUser.email == email.strip().lower()))
    if admin is None or not admin.is_active:
        await asyncio.to_thread(burn_admin_check, password)
        audit.record(session, "admin.sign_in_failed", ip=ip, email=email.strip().lower(), reason="unknown")
        await session.commit()
        raise SignInError("Email or password is incorrect.")
    if _locked(admin):
        audit.record(session, "admin.sign_in_blocked", admin_id=admin.id, ip=ip, reason="locked")
        await session.commit()
        raise SignInError("Too many attempts. Try again in a few minutes.")
    if not await asyncio.to_thread(check_admin_password, password, admin.password_hash):
        _register_failure(admin, settings)
        audit.record(session, "admin.sign_in_failed", admin_id=admin.id, ip=ip, reason="password")
        await session.commit()
        raise SignInError("Email or password is incorrect.")

    challenge = signing.sign(
        {"aid": str(admin.id)},
        secret=settings.secret_key,
        purpose=CHALLENGE_PURPOSE,
        ttl_seconds=CHALLENGE_TTL_SECONDS,
    )
    if admin.totp_enabled and admin.totp_secret:
        return PasswordStep(next="totp", challenge=challenge)

    if not admin.totp_secret:
        admin.totp_secret = totp.new_secret()
        await session.commit()
    uri = totp.provisioning_uri(admin.totp_secret, admin.email)
    return PasswordStep(
        next="totp_setup", challenge=challenge, secret=admin.totp_secret, qr_svg=totp.qr_svg_data_uri(uri)
    )


async def check_code(
    session: AsyncSession,
    settings: Settings,
    *,
    challenge: str,
    code: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[AdminUser, str]:
    """Verify the authenticator code and open a session. Returns the admin and the raw cookie token."""
    try:
        payload = signing.unsign(challenge, secret=settings.secret_key, purpose=CHALLENGE_PURPOSE)
        admin_id = uuid.UUID(str(payload["aid"]))
    except (signing.BadToken, KeyError, ValueError) as error:
        raise SignInError("Your sign-in expired. Start again.") from error

    admin = await session.get(AdminUser, admin_id)
    if admin is None or not admin.is_active or not admin.totp_secret:
        raise SignInError("Your sign-in expired. Start again.")
    if _locked(admin):
        raise SignInError("Too many attempts. Try again in a few minutes.")

    step = totp.matching_step(admin.totp_secret, code)
    if step is None or (admin.totp_last_step is not None and step <= admin.totp_last_step):
        _register_failure(admin, settings)
        audit.record(session, "admin.sign_in_failed", admin_id=admin.id, ip=ip, reason="code")
        await session.commit()
        raise SignInError("That code didn’t work. Check your authenticator app and try again.")

    first_setup = not admin.totp_enabled
    admin.totp_enabled = True
    admin.totp_last_step = step
    admin.failed_logins = 0
    admin.locked_until = None
    admin.last_login_at = utcnow()

    token = secrets.token_urlsafe(32)
    session.add(
        AdminSession(
            token_hash=hash_token(token),
            admin_id=admin.id,
            expires_at=utcnow() + timedelta(hours=settings.admin_session_hours),
            ip=ip,
            user_agent=(user_agent or "")[:512] or None,
        )
    )
    audit.record(session, "admin.signed_in", admin_id=admin.id, ip=ip, first_two_step_setup=first_setup)
    await session.commit()
    return admin, token


async def admin_for_token(session: AsyncSession, token: str) -> AdminUser | None:
    row = await session.scalar(
        select(AdminSession).where(
            AdminSession.token_hash == hash_token(token),
            AdminSession.revoked_at.is_(None),
            AdminSession.expires_at > utcnow(),
        )
    )
    if row is None:
        return None
    admin = await session.get(AdminUser, row.admin_id)
    return admin if admin is not None and admin.is_active else None


async def sign_out(session: AsyncSession, token: str, *, ip: str | None) -> None:
    row = await session.get(AdminSession, hash_token(token))
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        audit.record(session, "admin.signed_out", admin_id=row.admin_id, ip=ip)
        await session.commit()
