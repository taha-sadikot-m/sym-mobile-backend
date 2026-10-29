import logging
from typing import Any

from app.core.db import SessionLocal
from app.models.usage import AiCall

log = logging.getLogger("sym.ai")


async def record(**values: Any) -> None:
    """Log one AI call in its own transaction; never raises (logging must not break a request)."""
    try:
        async with SessionLocal() as session:
            session.add(AiCall(**values))
            await session.commit()
    except Exception:
        log.exception("Couldn't record AI usage")
