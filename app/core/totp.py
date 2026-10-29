import time

import pyotp
import segno

ISSUER = "SYM Admin"
STEP_SECONDS = 30


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_svg_data_uri(uri: str) -> str:
    return str(segno.make(uri, error="m").svg_data_uri(scale=5, border=2))


def matching_step(secret: str, code: str, *, now: float | None = None) -> int | None:
    """The time step a 6-digit code belongs to (allowing one step of clock drift), or None."""
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = int((now if now is not None else time.time()) // STEP_SECONDS)
    for step in (current, current - 1, current + 1):
        if pyotp.utils.strings_equal(totp.generate_otp(step), code):
            return step
    return None
