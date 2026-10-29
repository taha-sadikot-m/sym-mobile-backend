import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.core import ratelimit
from app.core.config import get_settings
from app.models import AdminRole
from app.modules.admin_auth import service
from app.modules.admin_auth.deps import COOKIE_NAME, CurrentAdmin, DbSession, client_ip, require_admin_header

router = APIRouter(
    prefix="/admin/api/auth", tags=["admin auth"], dependencies=[Depends(require_admin_header)]
)


class PasswordIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordOut(BaseModel):
    next: Literal["totp", "totp_setup"]
    challenge: str
    secret: str | None = None
    qr_svg: str | None = None


class CodeIn(BaseModel):
    challenge: str = Field(max_length=1024)
    code: str = Field(min_length=6, max_length=8)


class AdminOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: AdminRole


@router.post("/password")
async def password_step(body: PasswordIn, request: Request, session: DbSession) -> PasswordOut:
    # Per-address throttle as well as the per-account lockout, so guessing from one place is slow.
    await ratelimit.hit(f"admin-sign-in:ip:{client_ip(request)}", limit=20, per_seconds=15 * 60)
    try:
        step = await service.check_password(
            session, get_settings(), email=body.email, password=body.password, ip=client_ip(request)
        )
    except service.SignInError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    return PasswordOut(next=step.next, challenge=step.challenge, secret=step.secret, qr_svg=step.qr_svg)


@router.post("/code")
async def code_step(body: CodeIn, request: Request, response: Response, session: DbSession) -> AdminOut:
    settings = get_settings()
    await ratelimit.hit(f"admin-code:ip:{client_ip(request)}", limit=20, per_seconds=15 * 60)
    try:
        admin, token = await service.check_code(
            session,
            settings,
            challenge=body.challenge,
            code=body.code,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except service.SignInError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.admin_session_hours * 3600,
        path="/admin",
        httponly=True,
        secure=settings.is_deployed,
        samesite="strict",
    )
    return AdminOut(id=admin.id, email=admin.email, full_name=admin.full_name, role=admin.role)


@router.get("/me")
async def me(admin: CurrentAdmin) -> AdminOut:
    return AdminOut(id=admin.id, email=admin.email, full_name=admin.full_name, role=admin.role)


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(request: Request, response: Response, session: DbSession) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await service.sign_out(session, token, ip=client_ip(request))
    response.delete_cookie(COOKIE_NAME, path="/admin")
