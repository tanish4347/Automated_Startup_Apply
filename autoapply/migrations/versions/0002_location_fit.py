"""add jobs.location_fit

Revision ID: 0002_location_fit
Revises: 0001_baseline
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_location_fit"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("location_fit", sa.String(length=32), nullable=True))
        batch_op.create_index(batch_op.f("ix_jobs_location_fit"), ["location_fit"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_location_fit"))
        batch_op.drop_column("location_fit")
