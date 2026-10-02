"""companies.company_brief (+ fetched-at), generated_answer (answer tier 3 cache)

Revision ID: 0012_brief_generated
Revises: 0011_question_alias
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_brief_generated"
down_revision: Union[str, None] = "0011_question_alias"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("company_brief", sa.JSON(), nullable=True))
        b.add_column(sa.Column("company_brief_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "generated_answer",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_key", sa.String(length=128), nullable=False),
        sa.Column("company_key", sa.String(length=256), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("draft", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("context_digest", sa.String(length=40), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key", "company_key", name="uq_generated_answer_key_company"),
    )


def downgrade() -> None:
    op.drop_table("generated_answer")
    with op.batch_alter_table("companies") as b:
        b.drop_column("company_brief_at")
        b.drop_column("company_brief")
