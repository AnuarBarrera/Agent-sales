"""Add rejected sales playbook lifecycle state and review metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE salesplaybookstatus ADD VALUE IF NOT EXISTS 'rejected'")
    op.add_column("sales_playbooks", sa.Column("rejected_by", sa.UUID(), nullable=True))
    op.add_column("sales_playbooks", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sales_playbooks", sa.Column("rejection_reason", sa.String(length=5000), nullable=True))
    op.create_foreign_key(
        "fk_sales_playbooks_rejected_by_users",
        "sales_playbooks",
        "users",
        ["rejected_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sales_playbooks_rejected_by_users", "sales_playbooks", type_="foreignkey")
    op.drop_column("sales_playbooks", "rejection_reason")
    op.drop_column("sales_playbooks", "rejected_at")
    op.drop_column("sales_playbooks", "rejected_by")
