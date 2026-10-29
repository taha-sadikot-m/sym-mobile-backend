from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import client_ip as real_client_ip
from app.core.db import get_session
from app.models import AdminRole, AdminUser
from app.modules.admin_auth.service import admin_for_token

COOKIE_NAME = "sym_admin"
CSRF_HEADER = "X-SYM-Admin"

DbSession = Annotated[AsyncSession, Depends(get_session)]


client_ip = real_client_ip  # the one safe way to read the caller's address


async def require_admin_header(request: Request) -> None:
    """Blocks cross-site form posts: browsers can't add a custom header to another site's request."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing admin request header.")


async def current_admin(request: Request, session: DbSession) -> AdminUser:
    token = request.cookies.get(COOKIE_NAME)
    admin = await admin_for_token(session, token) if token else None
    if admin is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return admin


CurrentAdmin = Annotated[AdminUser, Depends(current_admin)]


def require_role(*roles: AdminRole) -> Callable[[AdminUser], Coroutine[Any, Any, AdminUser]]:
    """Super admins can do everything; other roles only what is listed."""

    async def check(admin: CurrentAdmin) -> AdminUser:
        if admin.role != AdminRole.SUPER_ADMIN and admin.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role can’t do this.")
        return admin

    return check
