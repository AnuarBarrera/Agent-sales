"""Persist section-aware sales playbook extraction audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_playbooks",
        sa.Column("generation_audit", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sales_playbooks", "generation_audit")
