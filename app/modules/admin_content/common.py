import uuid
from enum import Enum
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminRole, AdminUser
from app.modules.admin_auth.deps import require_role

# Content managers (and super admins) change content; every admin role can read it.
ContentEditor = Annotated[AdminUser, Depends(require_role(AdminRole.CONTENT_MANAGER))]
SuperAdmin = Annotated[AdminUser, Depends(require_role())]


class PageOut[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


async def paginate(
    session: AsyncSession, stmt: Select[Any], *, limit: int, offset: int
) -> tuple[list[Any], int]:
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(await session.scalars(stmt.limit(limit).offset(offset)))
    return rows, total


async def pairs[K, V](session: AsyncSession, stmt: Select[K, V]) -> dict[K, V]:
    """Run a two-column query (e.g. id, count) and return it as a dict."""
    return {key: value for key, value in await session.execute(stmt)}


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def apply_changes(model: Any, values: dict[str, Any]) -> dict[str, list[Any]]:
    """Set the given fields and return {field: [before, after]} for the ones that actually changed."""
    changes: dict[str, list[Any]] = {}
    for key, value in values.items():
        before = getattr(model, key)
        if _plain(before) != _plain(value):
            setattr(model, key, value)
            changes[key] = [_plain(before), _plain(value)]
    return changes


def not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found.")


def conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)
