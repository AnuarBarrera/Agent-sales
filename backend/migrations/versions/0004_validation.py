"""Add validation models and lead tracking fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-13

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create lead_validations table
    op.create_table(
        "lead_validations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prospection_runs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("check_type", sa.Enum("website", "google_presence", "meta_ads", name="validationchecktype"), nullable=False),
        sa.Column("result", sa.Enum("valid", "invalid", "active", "none", "not_found", "pending", "error", name="validationresult"), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_request_id", sa.String(100), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("lead_id", "check_type", "attempt", name="uq_lead_validation_lead_check_attempt"),
    )
    op.create_index("ix_lead_validations_lead_check", "lead_validations", ["lead_id", "check_type"])

    # Create validation_snapshots table
    op.create_table(
        "validation_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("website_result", sa.Enum("valid", "invalid", "active", "none", "not_found", "pending", "error", name="validationresult", create_type=False), nullable=True),
        sa.Column("google_presence_result", sa.Enum("valid", "invalid", "active", "none", "not_found", "pending", "error", name="validationresult", create_type=False), nullable=True),
        sa.Column("meta_ads_result", sa.Enum("valid", "invalid", "active", "none", "not_found", "pending", "error", name="validationresult", create_type=False), nullable=True),
        # Use String for profile to avoid enum creation issues - validation in application
        sa.Column("profile", sa.String(50), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("score_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("lead_id", name="uq_validation_snapshot_lead"),
    )

    # Add tracking fields to leads table
    op.add_column("leads", sa.Column("website_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("google_presence_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("meta_ads_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "meta_ads_checked_at")
    op.drop_column("leads", "google_presence_checked_at")
    op.drop_column("leads", "website_checked_at")
    op.drop_table("validation_snapshots")
    op.drop_table("lead_validations")

    op.execute("DROP TYPE IF EXISTS validationresult")
    op.execute("DROP TYPE IF EXISTS validationchecktype")
    # Note: leadprofile is used by leads table, don't drop it here
