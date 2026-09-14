"""Add pipeline_stages table and pipeline_stage_id to leads

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create pipeline_stages table
    op.create_table(
        "pipeline_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "key", name="uq_pipeline_stage_workspace_key"),
        sa.UniqueConstraint("workspace_id", "order", name="uq_pipeline_stage_workspace_order"),
    )

    # Add pipeline_stage_id column to leads table
    op.add_column("leads", sa.Column("pipeline_stage_id", postgresql.UUID(as_uuid=True), nullable=True, index=True))
    op.create_foreign_key(
        "fk_leads_pipeline_stage_id_pipeline_stages",
        "leads", "pipeline_stages",
        ["pipeline_stage_id"], ["id"],
        ondelete="SET NULL"
    )

    # Insert default pipeline stages for existing workspaces
    # This will be done via a data migration or application code


def downgrade() -> None:
    op.drop_constraint("fk_leads_pipeline_stage_id_pipeline_stages", "leads", type_="foreignkey")
    op.drop_column("leads", "pipeline_stage_id")
    op.drop_table("pipeline_stages")
