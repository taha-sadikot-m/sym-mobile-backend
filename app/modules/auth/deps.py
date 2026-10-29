from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tokens
from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.db import get_session
from app.models import Profile
from app.models.base import utcnow
from app.modules.auth.service import AuthError, check_allowed, ensure_profile
from app.web import users
from app.web.users import WebUser

bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class Student:
    user: WebUser
    profile: Profile

    @property
    def id(self) -> int:
        return self.user.id


def _unauthorized(message: str = "Please sign in again.", code: str = "session_expired") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail={"message": message, "code": code},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_student(
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Student:
    if credentials is None:
        raise _unauthorized("Sign in to continue.", "signed_out")
    try:
        payload = tokens.read_access_token(get_settings(), credentials.credentials)
        user_id = int(payload["sub"])
    except (tokens.InvalidToken, ValueError) as error:
        raise _unauthorized() from error
    row = (
        await session.execute(
            select(*users.COLUMNS, Profile)
            .outerjoin(Profile, Profile.user_id == users.users.c.id)
            .where(users.users.c.id == user_id)
        )
    ).first()
    if row is None:
        raise _unauthorized()
    user = WebUser(*row[:-1])
    profile = row[-1] or await ensure_profile(session, user.id)
    try:
        check_allowed(user, profile)
    except AuthError as error:
        raise HTTPException(error.status, detail={"message": str(error), "code": error.code}) from error
    # Cheap "last active" for the admin console, written at most every 5 minutes.
    if profile.last_seen_at is None or profile.last_seen_at < utcnow() - timedelta(minutes=5):
        profile.last_seen_at = utcnow()
        await session.commit()
    return Student(user=user, profile=profile)


CurrentStudent = Annotated[Student, Depends(current_student)]


def device_of(request: Request) -> str | None:
    return request.headers.get("x-device") or request.headers.get("user-agent")


def ip_of(request: Request) -> str:
    return client_ip(request) or "unknown"
