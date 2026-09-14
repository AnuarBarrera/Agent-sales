"""Allow invitation history while keeping pending emails unique.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_invitation_workspace_email", "invitations", type_="unique")
    op.create_index(
        "uq_invitation_workspace_email",
        "invitations",
        ["workspace_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_invitation_workspace_email", table_name="invitations")
    op.create_unique_constraint(
        "uq_invitation_workspace_email", "invitations", ["workspace_id", "email"]
    )
