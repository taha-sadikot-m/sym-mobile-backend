"""Password hashing.

Student accounts live in the web backend's `users` table, so their passwords must use Django's
exact format (`pbkdf2_sha256$<iterations>$<salt>$<base64 hash>`). That is what lets one account sign in
on both the website and the app. Admin console accounts are ours alone and use Argon2.
"""

import base64
import hashlib
import hmac
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Django 5.2's default. Existing web accounts all use this count.
DJANGO_ITERATIONS = 1_000_000
_SALT_CHARS = string.ascii_letters + string.digits


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return base64.b64encode(digest).decode("ascii")


def make_django_password(password: str, *, iterations: int = DJANGO_ITERATIONS) -> str:
    salt = "".join(secrets.choice(_SALT_CHARS) for _ in range(22))
    return f"pbkdf2_sha256${iterations}${salt}${_pbkdf2(password, salt, iterations)}"


def check_django_password(password: str, encoded: str) -> bool:
    """True if `password` matches a Django pbkdf2_sha256 hash. Unusable or unknown formats never match."""
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        rounds = int(iterations)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256" or rounds < 1:
        return False
    return hmac.compare_digest(_pbkdf2(password, salt, rounds), expected)


_argon2 = PasswordHasher()


def hash_admin_password(password: str) -> str:
    return _argon2.hash(password)


def check_admin_password(password: str, encoded: str) -> bool:
    try:
        return _argon2.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


# Spend the same time on unknown emails as on real ones, so sign-in timing doesn't reveal accounts.
_DUMMY_ADMIN_HASH = _argon2.hash(secrets.token_urlsafe(16))


def burn_admin_check(password: str) -> None:
    check_admin_password(password, _DUMMY_ADMIN_HASH)
