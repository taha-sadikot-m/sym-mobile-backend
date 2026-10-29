"""Alembic migrations for the `mobile` schema only.

The web backend's tables in `public` are owned by Django. These migrations never create, alter or drop
anything outside `mobile`, and autogenerate ignores every other schema.
"""

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import build_engine
from app.models import MOBILE_SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    if type_ == "table":
        return getattr(obj, "schema", None) == MOBILE_SCHEMA
    return True


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name == MOBILE_SCHEMA
    return True


def run(connection: Connection) -> None:
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {MOBILE_SCHEMA}"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=MOBILE_SCHEMA,
        include_schemas=True,
        include_object=include_object,
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = build_engine(get_settings())
    async with engine.begin() as conn:
        await conn.run_sync(run)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
asyncio.run(main())
