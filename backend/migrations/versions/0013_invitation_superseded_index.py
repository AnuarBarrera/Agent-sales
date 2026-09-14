"""Track replaced expired invitations separately from accepted invitations.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invitations", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_index("uq_invitation_workspace_email", table_name="invitations")
    op.create_index(
        "uq_invitation_workspace_email",
        "invitations",
        ["workspace_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_invitation_workspace_email", table_name="invitations")
    op.create_index(
        "uq_invitation_workspace_email",
        "invitations",
        ["workspace_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL"),
    )
    op.drop_column("invitations", "superseded_at")
