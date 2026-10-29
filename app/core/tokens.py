"""Student sign-in tokens.

Access tokens are short-lived JWTs the app sends as `Authorization: Bearer …`. Refresh tokens are random
strings stored hashed in `mobile.refresh_tokens`; each use replaces it with a new one, and reusing an
old one (a sign it was stolen) signs that device out.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt

from app.core.config import Settings
from app.models.base import utcnow

ALGORITHM = "HS256"
ISSUER = "sym-mobile"


class InvalidToken(Exception):
    pass


def make_access_token(settings: Settings, *, user_id: int, family_id: uuid.UUID) -> tuple[str, int]:
    now = utcnow()
    lifetime = settings.access_token_minutes * 60
    payload = {
        "sub": str(user_id),
        "sid": str(family_id),
        "typ": "access",
        "iss": ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=lifetime)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), lifetime


def read_access_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["sub", "exp", "iat", "sid"]},
        )
    except jwt.PyJWTError as error:
        raise InvalidToken(str(error)) from error
    if payload.get("typ") != "access":
        raise InvalidToken("not an access token")
    return payload


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_code() -> str:
    """A 6-digit code for email verification and password resets."""
    return f"{secrets.randbelow(1_000_000):06d}"
