from sqlalchemy import text

from app.core.db import engine
from app.web.contract import CONTRACT, Snapshot, check_contract, compare


def _snapshot_matching_contract() -> dict[str, dict[str, tuple[str, bool]]]:
    return {table: dict(columns) for table, columns in CONTRACT.items()}


def test_matching_schema_has_no_drift() -> None:
    assert compare(Snapshot(_snapshot_matching_contract())) == []


def test_extra_nullable_columns_are_fine() -> None:
    columns = _snapshot_matching_contract()
    columns["users"]["nickname"] = ("character varying", True)
    assert compare(Snapshot(columns)) == []


def test_detects_missing_renamed_and_retyped_columns() -> None:
    columns = _snapshot_matching_contract()
    columns["users"]["name"] = columns["users"].pop("full_name")
    columns["debate_results"]["winner_id"] = ("uuid", True)
    columns["debate_sessions"]["opponent_id"] = ("bigint", False)
    del columns["user_debate_stats"]
    problems = {str(item) for item in compare(Snapshot(columns))}
    assert problems == {
        "users.full_name: column is missing",
        "debate_results.winner_id: type changed from bigint to uuid",
        "debate_sessions.opponent_id: changed from nullable to required",
        "user_debate_stats: table is missing",
    }


def test_detects_new_required_column_in_a_table_we_write() -> None:
    columns = _snapshot_matching_contract()
    columns["users"]["phone"] = ("character varying", False)
    columns["organizations"]["slug"] = ("character varying", False)
    snapshot = Snapshot(columns, frozenset({("users", "phone"), ("organizations", "slug")}))
    assert [str(item) for item in compare(snapshot)] == [
        "users.phone: new required column the mobile backend does not fill"
    ]


async def test_real_web_schema_matches_the_contract() -> None:
    async with engine.connect() as conn:
        assert await check_contract(conn) == []


async def test_catches_a_column_renamed_by_the_web_backend() -> None:
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("ALTER TABLE public.users RENAME COLUMN full_name TO display_name"))
            problems = [str(item) for item in await check_contract(conn)]
        finally:
            await transaction.rollback()
    assert "users.full_name: column is missing" in problems
