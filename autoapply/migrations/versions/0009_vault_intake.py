"""vault intake: source/status on fact tables, answer suggestions and catalog metadata, vault_policy

Revision ID: 0009_vault_intake
Revises: 0008_form_question
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_vault_intake"
down_revision: Union[str, None] = "0008_form_question"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FACT_TABLES = ("vault_identity", "vault_education", "vault_employment", "vault_project", "vault_skill")


def upgrade() -> None:
    for table in FACT_TABLES:
        with op.batch_alter_table(table, schema=None) as b:
            b.add_column(sa.Column("source", sa.String(length=64), nullable=True))
            b.add_column(sa.Column("status", sa.String(length=64), nullable=True))
    with op.batch_alter_table("vault_answer", schema=None) as b:
        b.add_column(sa.Column("suggested_answer", sa.Text(), nullable=True))
        b.add_column(sa.Column("field_type", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("weight", sa.Float(), nullable=True))
        b.add_column(sa.Column("intake_pass", sa.String(length=16), nullable=True))
    op.create_table(
        "vault_policy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("vault_policy")
    with op.batch_alter_table("vault_answer", schema=None) as b:
        for col in ("intake_pass", "weight", "field_type", "suggested_answer"):
            b.drop_column(col)
    for table in FACT_TABLES:
        with op.batch_alter_table(table, schema=None) as b:
            b.drop_column("status")
            b.drop_column("source")
