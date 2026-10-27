"""Add sales playbook generation audit metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_playbooks", sa.Column("generation_provider", sa.String(length=100), nullable=True))
    op.add_column("sales_playbooks", sa.Column("generation_operation", sa.String(length=100), nullable=True))
    op.add_column("sales_playbooks", sa.Column("generation_latency_ms", sa.Integer(), nullable=True))
    op.add_column("sales_playbooks", sa.Column("generation_success", sa.Boolean(), nullable=True))
    op.add_column("sales_playbooks", sa.Column("generation_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_playbooks", "generation_tokens")
    op.drop_column("sales_playbooks", "generation_success")
    op.drop_column("sales_playbooks", "generation_latency_ms")
    op.drop_column("sales_playbooks", "generation_operation")
    op.drop_column("sales_playbooks", "generation_provider")
