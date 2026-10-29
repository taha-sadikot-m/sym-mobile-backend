"""The web backend's tables that the mobile backend reads or writes, column by column.

The web (Django) backend owns these tables and may change them at any time. Before we touch them we
check that every column we rely on still exists with the same type and nullability. If not, the
service refuses to start (and /health reports the drift), instead of silently corrupting web data.

Types are PostgreSQL `information_schema.columns.data_type` values.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

TS = "timestamp with time zone"
VARCHAR = "character varying"

# column -> (data_type, nullable)
Columns = dict[str, tuple[str, bool]]

CONTRACT: dict[str, Columns] = {
    "users": {
        "id": ("bigint", False),
        "password": (VARCHAR, False),
        "last_login": (TS, True),
        "is_superuser": ("boolean", False),
        "email": (VARCHAR, False),
        "full_name": (VARCHAR, False),
        "user_type": (VARCHAR, False),
        "member_id": (VARCHAR, True),
        "is_active": ("boolean", False),
        "is_email_verified": ("boolean", False),
        "is_staff": ("boolean", False),
        "must_change_password": ("boolean", False),
        "created_at": (TS, False),
        "updated_at": (TS, False),
        "last_login_at": (TS, True),
        "organization_id": ("bigint", True),
        "has_completed_onboarding": ("boolean", False),
        "current_rank": ("integer", False),
        "rank_tier": (VARCHAR, False),
        "total_debates": ("integer", False),
        "total_wins": ("integer", False),
        "win_rate": ("numeric", False),
        "current_win_streak": ("integer", False),
    },
    "organizations": {
        "id": ("bigint", False),
        "name": (VARCHAR, False),
        "is_active": ("boolean", False),
    },
    "debate_sessions": {
        "id": ("uuid", False),
        "mode": (VARCHAR, False),
        "status": (VARCHAR, False),
        "is_ai_opponent": ("boolean", False),
        "topic": ("text", False),
        "topic_type": (VARCHAR, False),
        "creator_position": (VARCHAR, False),
        "opponent_position": (VARCHAR, False),
        "difficulty": (VARCHAR, False),
        "opening_speaker": (VARCHAR, False),
        "total_rounds": ("integer", False),
        "current_round": ("integer", False),
        "time_per_round": ("integer", False),
        "current_turn": (VARCHAR, False),
        "scheduled_at": (TS, True),
        "started_at": (TS, True),
        "ended_at": (TS, True),
        "created_at": (TS, False),
        "updated_at": (TS, False),
        "metadata": ("jsonb", False),
        "creator_id": ("bigint", False),
        "opponent_id": ("bigint", True),
        "organization_id": ("bigint", True),
        "event_id": ("uuid", True),
    },
    "debate_messages": {
        "id": ("uuid", False),
        "is_ai_message": ("boolean", False),
        "message_type": (VARCHAR, False),
        "content": ("text", False),
        "audio_file": (VARCHAR, True),
        "audio_duration": ("integer", True),
        "round_number": ("integer", False),
        "sequence_number": ("integer", False),
        "created_at": (TS, False),
        "metadata": ("jsonb", False),
        "debate_id": ("uuid", False),
        "sender_id": ("bigint", True),
    },
    "debate_results": {
        "id": ("uuid", False),
        "creator_logical_score": ("numeric", False),
        "creator_emotional_score": ("numeric", False),
        "creator_rebuttal_score": ("numeric", False),
        "creator_total_score": ("numeric", False),
        "opponent_logical_score": ("numeric", False),
        "opponent_emotional_score": ("numeric", False),
        "opponent_rebuttal_score": ("numeric", False),
        "opponent_total_score": ("numeric", False),
        "judged_by": (VARCHAR, False),
        "judge_notes": ("text", False),
        "creator_rank_change": ("integer", False),
        "opponent_rank_change": ("integer", False),
        "created_at": (TS, False),
        "metadata": ("jsonb", False),
        "debate_id": ("uuid", False),
        "winner_id": ("bigint", True),
    },
    "user_debate_stats": {
        "user_id": ("bigint", False),
        "total_debates": ("integer", False),
        "total_wins": ("integer", False),
        "total_losses": ("integer", False),
        "total_draws": ("integer", False),
        "practice_debates": ("integer", False),
        "pvp_debates": ("integer", False),
        "event_debates": ("integer", False),
        "avg_logical_score": ("numeric", False),
        "avg_emotional_score": ("numeric", False),
        "avg_rebuttal_score": ("numeric", False),
        "avg_total_score": ("numeric", False),
        "current_rank": ("integer", False),
        "peak_rank": ("integer", False),
        "rank_tier": (VARCHAR, False),
        "current_win_streak": ("integer", False),
        "longest_win_streak": ("integer", False),
        "last_debate_at": (TS, True),
        "updated_at": (TS, False),
    },
}


# Tables the mobile backend INSERTs into; every required column in them must be in CONTRACT.
WRITTEN_TABLES = frozenset(
    {"users", "debate_sessions", "debate_messages", "debate_results", "user_debate_stats"}
)


@dataclass(frozen=True)
class Drift:
    table: str
    column: str | None
    problem: str

    def __str__(self) -> str:
        where = f"{self.table}.{self.column}" if self.column else self.table
        return f"{where}: {self.problem}"


@dataclass(frozen=True)
class Snapshot:
    """What the database has for the contract tables."""

    columns: dict[str, Columns]
    # NOT NULL columns with no database default: our INSERTs must fill every one of them.
    required_without_default: frozenset[tuple[str, str]] = frozenset()


def compare(snapshot: Snapshot, contract: dict[str, Columns] = CONTRACT) -> list[Drift]:
    """Differences between what we rely on and what the database has. Extra nullable columns are fine."""
    drift: list[Drift] = []
    for table, expected_columns in contract.items():
        found = snapshot.columns.get(table)
        if found is None:
            drift.append(Drift(table, None, "table is missing"))
            continue
        for column, (data_type, nullable) in expected_columns.items():
            if column not in found:
                drift.append(Drift(table, column, "column is missing"))
                continue
            actual_type, actual_nullable = found[column]
            if actual_type != data_type:
                drift.append(Drift(table, column, f"type changed from {data_type} to {actual_type}"))
            if actual_nullable != nullable:
                was, now = ("nullable", "required") if nullable else ("required", "nullable")
                drift.append(Drift(table, column, f"changed from {was} to {now}"))
    for table, column in sorted(snapshot.required_without_default):
        if table in WRITTEN_TABLES and column not in contract.get(table, {}):
            drift.append(Drift(table, column, "new required column the mobile backend does not fill"))
    return drift


async def read_snapshot(conn: AsyncConnection) -> Snapshot:
    rows = await conn.execute(
        text(
            """
            SELECT table_name, column_name, data_type, is_nullable = 'YES' AS nullable,
                   column_default IS NULL AND is_identity = 'NO' AS no_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(:tables)
            """
        ),
        {"tables": list(CONTRACT)},
    )
    columns: dict[str, Columns] = {}
    required: set[tuple[str, str]] = set()
    for table, column, data_type, nullable, no_default in rows:
        columns.setdefault(table, {})[column] = (data_type, nullable)
        if not nullable and no_default:
            required.add((table, column))
    return Snapshot(columns, frozenset(required))


async def check_contract(conn: AsyncConnection) -> list[Drift]:
    return compare(await read_snapshot(conn))
