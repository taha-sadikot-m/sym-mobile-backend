from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings


def build_engine(settings: Settings) -> AsyncEngine:
    connect_args: dict[str, object] = {"server_settings": {"application_name": "sym-mobile-backend"}}
    if settings.database_pooled:
        connect_args["statement_cache_size"] = 0
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
    )


engine = build_engine(get_settings())
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
