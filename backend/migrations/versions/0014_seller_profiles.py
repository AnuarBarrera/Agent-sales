"""Add seller-side business profiles."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seller_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("business_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("offer_summary", sa.Text(), nullable=True),
        sa.Column("target_customer_summary", sa.Text(), nullable=True),
        sa.Column("location_or_market", sa.String(length=255), nullable=True),
        sa.Column("price_context", sa.String(length=500), nullable=True),
        sa.Column("primary_goal", sa.String(length=500), nullable=True),
        sa.Column("differentiators", sa.Text(), nullable=True),
        sa.Column("sales_constraints", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_seller_profile_workspace"),
    )
    op.create_index("ix_seller_profiles_workspace_id", "seller_profiles", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_seller_profiles_workspace_id", table_name="seller_profiles")
    op.drop_table("seller_profiles")
