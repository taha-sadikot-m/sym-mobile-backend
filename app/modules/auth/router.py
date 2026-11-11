from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, status

from app.core import ratelimit
from app.core.config import get_settings
from app.modules.auth import service
from app.modules.auth.deps import Session, device_of, ip_of
from app.modules.auth.schemas import (
    AcceptedOut,
    CodeIn,
    EmailIn,
    PendingOut,
    RefreshIn,
    ResetIn,
    SignInIn,
    SignUpIn,
    TokensOut,
    birth_year_from_age,
)

router = APIRouter(prefix="/v1/auth", tags=["student auth"])

QUARTER_HOUR = 15 * 60
HOUR = 60 * 60


async def _run[T](work: Callable[[], Awaitable[T]]) -> T:
    try:
        return await work()
    except service.AuthError as error:
        raise HTTPException(error.status, detail={"message": str(error), "code": error.code}) from error


def _tokens(pair: service.TokenPair) -> TokensOut:
    return TokensOut(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post("/sign-up", status_code=status.HTTP_201_CREATED)
async def sign_up(body: SignUpIn, request: Request, session: Session) -> PendingOut:
    """Create an account (shared with the website); the student then enters the code emailed to them."""
    await ratelimit.hit(f"sign-up:ip:{ip_of(request)}", limit=10, per_seconds=HOUR)
    user = await _run(
        lambda: service.sign_up(
            session,
            email=str(body.email),
            password=body.password,
            full_name=body.full_name,
            username=body.username,
            birth_year=birth_year_from_age(body.age),
            institution=body.institution,
            city=body.city,
            country=body.country,
            mun_experience=body.mun_experience,
            comfort_language=body.comfort_language,
        )
    )
    return PendingOut(email=user.email)


@router.post("/sign-in")
async def sign_in(body: SignInIn, request: Request, session: Session) -> TokensOut:
    email = str(body.email).lower()
    await ratelimit.hit(f"sign-in:ip:{ip_of(request)}", limit=30, per_seconds=QUARTER_HOUR)
    await ratelimit.hit(f"sign-in:email:{email}", limit=10, per_seconds=QUARTER_HOUR)
    pair = await _run(
        lambda: service.sign_in(
            session, get_settings(), email=str(body.email), password=body.password, device=device_of(request)
        )
    )
    await ratelimit.clear(f"sign-in:email:{email}")
    return _tokens(pair)


@router.post("/verify-email")
async def verify_email(body: CodeIn, request: Request, session: Session) -> TokensOut:
    """Confirm the email with the 6-digit code; signs the student in."""
    await ratelimit.hit(f"code:email:{str(body.email).lower()}", limit=10, per_seconds=QUARTER_HOUR)
    await ratelimit.hit(f"code:ip:{ip_of(request)}", limit=30, per_seconds=QUARTER_HOUR)
    return _tokens(
        await _run(
            lambda: service.verify_email(
                session, get_settings(), email=str(body.email), code=body.code, device=device_of(request)
            )
        )
    )


@router.post("/resend-code", status_code=status.HTTP_202_ACCEPTED)
async def resend_code(body: EmailIn, request: Request, session: Session) -> AcceptedOut:
    await ratelimit.hit(f"send:email:{str(body.email).lower()}", limit=5, per_seconds=HOUR)
    await ratelimit.hit(f"send:email-day:{str(body.email).lower()}", limit=15, per_seconds=24 * HOUR)
    await ratelimit.hit(f"send:ip:{ip_of(request)}", limit=20, per_seconds=HOUR)
    await service.resend_verification(session, email=str(body.email))
    return AcceptedOut(message="If that email needs confirming, a new code is on its way.")


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(body: EmailIn, request: Request, session: Session) -> AcceptedOut:
    await ratelimit.hit(f"send:email:{str(body.email).lower()}", limit=5, per_seconds=HOUR)
    await ratelimit.hit(f"send:email-day:{str(body.email).lower()}", limit=15, per_seconds=24 * HOUR)
    await ratelimit.hit(f"send:ip:{ip_of(request)}", limit=20, per_seconds=HOUR)
    await service.forgot_password(session, email=str(body.email))
    return AcceptedOut(
        message="If there’s an account with that email, we’ve sent a code to reset the password."
    )


@router.post("/reset-password")
async def reset_password(body: ResetIn, request: Request, session: Session) -> TokensOut:
    await ratelimit.hit(f"code:email:{str(body.email).lower()}", limit=10, per_seconds=QUARTER_HOUR)
    await ratelimit.hit(f"code:ip:{ip_of(request)}", limit=30, per_seconds=QUARTER_HOUR)
    return _tokens(
        await _run(
            lambda: service.reset_password(
                session,
                get_settings(),
                email=str(body.email),
                code=body.code,
                new_password=body.new_password,
                device=device_of(request),
            )
        )
    )


@router.post("/refresh")
async def refresh(body: RefreshIn, request: Request, session: Session) -> TokensOut:
    return _tokens(
        await _run(
            lambda: service.refresh(
                session, get_settings(), refresh_token=body.refresh_token, device=device_of(request)
            )
        )
    )


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(body: RefreshIn, session: Session) -> None:
    await service.sign_out(session, refresh_token=body.refresh_token)
