"""Workspace-scoped client keys for retry-safe playbook creation."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_playbooks", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column("sales_playbooks", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_sales_playbook_workspace_idempotency_key", "sales_playbooks", ["workspace_id", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_sales_playbook_workspace_idempotency_key", "sales_playbooks", type_="unique")
    op.drop_column("sales_playbooks", "request_fingerprint")
    op.drop_column("sales_playbooks", "idempotency_key")
