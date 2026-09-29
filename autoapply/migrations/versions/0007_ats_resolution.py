"""ATS resolution state on companies

Revision ID: 0007_ats_resolution
Revises: 0006_company_universe
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_ats_resolution"
down_revision: Union[str, None] = "0006_company_universe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ats_confidence", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("ats_evidence", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("ats_resolved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("ats_last_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("ats_resolve_failures", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("needs_generic_crawl", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        for col in ("needs_generic_crawl", "ats_resolve_failures", "ats_last_attempt_at", "ats_resolved_at",
                    "ats_evidence", "ats_confidence"):
            batch_op.drop_column(col)
