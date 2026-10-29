from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.content.settings import LABELS, SETTINGS
from app.core import audit
from app.models import AdminUser, AppSetting
from app.models.base import utcnow
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import SuperAdmin, not_found

router = APIRouter(
    prefix="/admin/api/settings", tags=["admin settings"], dependencies=[Depends(require_admin_header)]
)


class SettingOut(BaseModel):
    key: str
    label: str
    value: dict[str, Any]
    defaults: dict[str, Any]
    is_default: bool
    updated_at: datetime | None
    updated_by: str | None


class SettingIn(BaseModel):
    value: dict[str, Any]


def _validate(key: str, value: dict[str, Any]) -> dict[str, Any]:
    model = SETTINGS.get(key)
    if model is None:
        raise not_found("Setting")
    try:
        return model.model_validate(value).model_dump(mode="json")
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        message = first["msg"].removeprefix("Value error, ")
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{where}: {message}" if where else message
        ) from error


async def _out(session: DbSession, key: str, row: AppSetting | None) -> SettingOut:
    defaults = SETTINGS[key]().model_dump(mode="json")
    email = (
        await session.scalar(select(AdminUser.email).where(AdminUser.id == row.updated_by))
        if row and row.updated_by
        else None
    )
    return SettingOut(
        key=key,
        label=LABELS[key],
        value=row.value if row else defaults,
        defaults=defaults,
        is_default=row is None,
        updated_at=row.updated_at if row else None,
        updated_by=email,
    )


@router.get("")
async def list_settings(_: CurrentAdmin, session: DbSession) -> list[SettingOut]:
    rows = {row.key: row for row in await session.scalars(select(AppSetting))}
    return [await _out(session, key, rows.get(key)) for key in SETTINGS]


@router.put("/{key}")
async def update_setting(
    key: str, body: SettingIn, admin: SuperAdmin, request: Request, session: DbSession
) -> SettingOut:
    value = _validate(key, body.value)
    row = await session.get(AppSetting, key)
    before = row.value if row else SETTINGS[key]().model_dump(mode="json")
    if before != value:
        if row is None:
            row = AppSetting(key=key, value=value, updated_by=admin.id)
            session.add(row)
        else:
            row.value = value
            row.updated_by = admin.id
            row.updated_at = utcnow()
        changes = {
            name: [before.get(name), value.get(name)] for name in value if before.get(name) != value.get(name)
        }
        audit.record(
            session,
            "setting.updated",
            admin_id=admin.id,
            target_type="setting",
            target_id=key,
            ip=client_ip(request),
            changes=changes,
        )
        await session.commit()
    return await _out(session, key, row)


@router.post("/{key}/reset")
async def reset_setting(key: str, admin: SuperAdmin, request: Request, session: DbSession) -> SettingOut:
    if key not in SETTINGS:
        raise not_found("Setting")
    row = await session.get(AppSetting, key)
    if row is not None:
        await session.delete(row)
        audit.record(
            session,
            "setting.reset",
            admin_id=admin.id,
            target_type="setting",
            target_id=key,
            ip=client_ip(request),
        )
        await session.commit()
    return await _out(session, key, None)
