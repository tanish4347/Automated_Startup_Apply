"""Add typed, scoped knowledge metadata and distinguish candidate profile concepts."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_vault_concepts"
down_revision: Union[str, Sequence[str], None] = "0013_attempt_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vault_identity", sa.Column("permanent_city", sa.String(length=128), nullable=True))
    op.add_column("vault_identity", sa.Column("permanent_country", sa.String(length=128), nullable=True))
    op.add_column("vault_education", sa.Column("degree_level", sa.String(length=64), nullable=True))
    op.add_column("vault_education", sa.Column("specialization", sa.String(length=128), nullable=True))
    op.add_column("vault_education", sa.Column("department", sa.String(length=128), nullable=True))
    op.add_column("vault_answer", sa.Column("value_json", sa.JSON(), nullable=True))
    op.add_column("vault_answer", sa.Column("value_type", sa.String(length=32), nullable=True))
    op.add_column("vault_answer", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("vault_answer", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vault_answer", sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vault_answer", sa.Column("scope", sa.String(length=32), nullable=False, server_default="candidate"))
    op.add_column("vault_answer", sa.Column("conditions", sa.JSON(), nullable=True))
    op.add_column("vault_answer", sa.Column("confirmed_by_user", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("vault_answer", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("vault_answer", "confirmed_at")
    op.drop_column("vault_answer", "confirmed_by_user")
    op.drop_column("vault_answer", "conditions")
    op.drop_column("vault_answer", "scope")
    op.drop_column("vault_answer", "valid_until")
    op.drop_column("vault_answer", "valid_from")
    op.drop_column("vault_answer", "confidence")
    op.drop_column("vault_answer", "value_type")
    op.drop_column("vault_answer", "value_json")
    op.drop_column("vault_education", "department")
    op.drop_column("vault_education", "specialization")
    op.drop_column("vault_education", "degree_level")
    op.drop_column("vault_identity", "permanent_country")
    op.drop_column("vault_identity", "permanent_city")
