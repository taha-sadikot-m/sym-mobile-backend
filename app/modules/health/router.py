from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine
from app.web.contract import check_contract

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    database: Literal["ok", "unreachable"]
    web_tables: Literal["ok", "changed", "unknown"]
    web_table_changes: list[str] = []


@router.api_route("/health", methods=["GET", "HEAD"])
async def health(response: Response) -> Health:
    settings = get_settings()
    database: Literal["ok", "unreachable"] = "ok"
    web_tables: Literal["ok", "changed", "unknown"] = "unknown"
    changes: list[str] = []
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            drift = await check_contract(conn)
        web_tables = "changed" if drift else "ok"
        changes = [str(item) for item in drift]
    except Exception:
        database = "unreachable"
    healthy = database == "ok" and web_tables == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    if settings.is_deployed:
        # Don't describe the database to strangers; System health in the admin console shows the details.
        changes = [f"{len(changes)} change(s) — see System in the admin console"] if changes else []
    return Health(
        status="ok" if healthy else "degraded",
        version=settings.version,
        environment=settings.environment,
        database=database,
        web_tables=web_tables,
        web_table_changes=changes,
    )
