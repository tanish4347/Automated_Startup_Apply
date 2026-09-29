"""company universe: companies.website, companies.seed_sources, company_aliases (merge audit)

Revision ID: 0006_company_universe
Revises: 0005_source_runs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_company_universe"
down_revision: Union[str, None] = "0005_source_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("website", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("seed_sources", sa.JSON(), nullable=True))
    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("seed_source", sa.String(length=64), nullable=False),
        sa.Column("match_type", sa.String(length=16), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_company_aliases_company_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "alias", "seed_source", name="uq_company_aliases"),
    )
    op.create_index("ix_company_aliases_company_id", "company_aliases", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_company_aliases_company_id", table_name="company_aliases")
    op.drop_table("company_aliases")
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("seed_sources")
        batch_op.drop_column("website")
