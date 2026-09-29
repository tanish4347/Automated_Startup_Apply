"""source_runs: per-source run log, request counts for daily budgets, degraded status

Revision ID: 0005_source_runs
Revises: 0004_backfill_dedup_v2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_source_runs"
down_revision: Union[str, None] = "0004_backfill_dedup_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("new", sa.Integer(), nullable=False),
        sa.Column("merged", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_runs_source", "source_runs", ["source"], unique=False)
    op.create_index("ix_source_runs_started_at", "source_runs", ["started_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_source_runs_started_at", table_name="source_runs")
    op.drop_index("ix_source_runs_source", table_name="source_runs")
    op.drop_table("source_runs")
