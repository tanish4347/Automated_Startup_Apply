"""Persist application stage transitions, action proofs, and recovery attempts."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_attempt_events"
down_revision: Union[str, Sequence[str], None] = "c13ae57ab5bd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_attempt_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attempt_id", sa.Integer(), sa.ForeignKey("application_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=True),
        sa.Column("expected", sa.JSON(), nullable=True),
        sa.Column("observed", sa.JSON(), nullable=True),
        sa.Column("verified", sa.String(length=16), nullable=True),
        sa.Column("recovery_level", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(length=120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("ix_application_attempt_events_attempt_id", "application_attempt_events", ["attempt_id"])
    op.create_index("ix_application_attempt_events_stage", "application_attempt_events", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_application_attempt_events_stage", table_name="application_attempt_events")
    op.drop_index("ix_application_attempt_events_attempt_id", table_name="application_attempt_events")
    op.drop_table("application_attempt_events")
