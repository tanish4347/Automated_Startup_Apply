"""apply harness: attempts, dedup-cluster claims, resolved apply URLs

Revision ID: 0010_apply_harness
Revises: 0009_vault_intake
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_apply_harness"
down_revision: Union[str, None] = "0009_vault_intake"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as b:
        b.add_column(sa.Column("resolved_apply_url", sa.Text(), nullable=True))
        b.add_column(sa.Column("apply_resolved_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("apply_resolve_note", sa.Text(), nullable=True))
    with op.batch_alter_table("applications", schema=None) as b:
        b.add_column(sa.Column("dedup_cluster", sa.String(length=512), nullable=True))
        b.add_column(sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
        b.create_index("ix_applications_dedup_cluster", ["dedup_cluster"])
        b.create_index("ix_applications_last_attempt_at", ["last_attempt_at"])
    op.create_table(
        "application_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("park_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("filled_fields", sa.JSON(), nullable=True),
        sa.Column("unfilled_fields", sa.JSON(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("form_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("success_evidence", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("blocked_requests", sa.JSON(), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], name="fk_attempts_application_id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_attempts_job_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_attempts_application_id", "application_attempts", ["application_id"])
    op.create_index("ix_application_attempts_job_id", "application_attempts", ["job_id"])
    op.create_index("ix_application_attempts_form_fingerprint", "application_attempts", ["form_fingerprint"])
    op.create_index("ix_application_attempts_review_status", "application_attempts", ["review_status"])
    op.create_table(
        "application_claims",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dedup_cluster", sa.String(length=512), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], name="fk_claims_application_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_cluster"),
    )


def downgrade() -> None:
    op.drop_table("application_claims")
    op.drop_table("application_attempts")
    with op.batch_alter_table("applications", schema=None) as b:
        b.drop_index("ix_applications_last_attempt_at")
        b.drop_index("ix_applications_dedup_cluster")
        b.drop_column("last_attempt_at")
        b.drop_column("dedup_cluster")
    with op.batch_alter_table("jobs", schema=None) as b:
        for c in ("apply_resolve_note", "apply_resolved_at", "resolved_apply_url"):
            b.drop_column(c)
