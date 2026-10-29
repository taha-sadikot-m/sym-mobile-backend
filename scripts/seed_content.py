"""Load the starting content (countries, SYM catalogue, debate topics, default seats).

    uv run python -m scripts.seed_content

Safe to run again: only missing records are added and admin edits are kept.
"""

import asyncio

from app.content.seeding import seed_all
from app.core import audit
from app.core.db import SessionLocal, engine


async def main() -> None:
    async with SessionLocal() as session:
        result = await seed_all(session)
        audit.record(
            session,
            "content.seeded",
            via="cli",
            countries_added=result.countries_added,
            committees_created=result.catalog.committees.created,
            agendas_created=result.catalog.agendas.created,
            topics_added=result.topics_added,
        )
        await session.commit()
    await engine.dispose()
    catalog = result.catalog
    print(
        f"Countries added: {result.countries_added}\n"
        f"Committees: {catalog.committees.created} new, {catalog.committees.updated} updated\n"
        f"Agendas: {catalog.agendas.created} new, {catalog.agendas.updated} updated\n"
        f"Debate topics added: {result.topics_added}\n"
        f"Committees given default seats: {result.committees_seated}"
    )


if __name__ == "__main__":
    asyncio.run(main())
