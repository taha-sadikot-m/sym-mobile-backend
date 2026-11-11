"""comfort language, ai call language, usage daily

Revision ID: 9a819d995524
Revises: b0193d082c6d
Create Date: 2026-11-12 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "9a819d995524"
down_revision: str | None = "b0193d082c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("comfort_language", sa.String(length=8), server_default="en", nullable=False),
        schema="mobile",
    )
    op.add_column("ai_calls", sa.Column("language", sa.String(length=16), nullable=True), schema="mobile")
    op.create_table(
        "usage_daily",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scored_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chat_messages", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("user_id", "day", name=op.f("pk_usage_daily")),
        schema="mobile",
    )


def downgrade() -> None:
    op.drop_table("usage_daily", schema="mobile")
    op.drop_column("ai_calls", "language", schema="mobile")
    op.drop_column("profiles", "comfort_language", schema="mobile")
