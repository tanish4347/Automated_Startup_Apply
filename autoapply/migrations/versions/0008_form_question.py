"""form_question: harvested application-form questions

Revision ID: 0008_form_question
Revises: 0007_ats_resolution
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_form_question"
down_revision: Union[str, None] = "0007_ats_resolution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "form_question",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("canonical_key", sa.String(length=96), nullable=True),
        sa.Column("raw_label", sa.Text(), nullable=False),
        sa.Column("field_type", sa.String(length=32), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=True),
        sa.Column("section", sa.String(length=32), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("company_key", sa.String(length=256), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("example_url", sa.Text(), nullable=True),
        sa.Column("times_seen", sa.Integer(), nullable=False),
        sa.Column("distinct_companies", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_form_question_company_id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_form_question_job_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "company_key", "raw_label", "field_type", name="uq_form_question"),
    )
    op.create_index("ix_form_question_platform", "form_question", ["platform"])
    op.create_index("ix_form_question_canonical_key", "form_question", ["canonical_key"])
    op.create_index("ix_form_question_company_id", "form_question", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_form_question_company_id", table_name="form_question")
    op.drop_index("ix_form_question_canonical_key", table_name="form_question")
    op.drop_index("ix_form_question_platform", table_name="form_question")
    op.drop_table("form_question")
