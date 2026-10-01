"""question_alias: form label -> canonical_key for the answer engine

Revision ID: 0011_question_alias
Revises: 0010_apply_harness
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_question_alias"
down_revision: Union[str, None] = "0010_apply_harness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_alias",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alias", sa.String(length=1000), nullable=False),
        sa.Column("canonical_key", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias"),
    )
    op.create_index("ix_question_alias_canonical_key", "question_alias", ["canonical_key"])


def downgrade() -> None:
    op.drop_index("ix_question_alias_canonical_key", table_name="question_alias")
    op.drop_table("question_alias")
