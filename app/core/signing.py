"""Small signed, expiring tokens (used between the password and authenticator-code steps of admin sign-in)."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class BadToken(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(payload: dict[str, Any], *, secret: str, purpose: str, ttl_seconds: int) -> str:
    body = _b64(json.dumps({**payload, "p": purpose, "exp": int(time.time()) + ttl_seconds}).encode())
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def unsign(token: str, *, secret: str, purpose: str) -> dict[str, Any]:
    try:
        body, mac = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(mac)):
            raise BadToken("bad signature")
        payload: dict[str, Any] = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as error:
        raise BadToken("malformed") from error
    if payload.get("p") != purpose:
        raise BadToken("wrong purpose")
    if int(payload.get("exp", 0)) < time.time():
        raise BadToken("expired")
    return payload
