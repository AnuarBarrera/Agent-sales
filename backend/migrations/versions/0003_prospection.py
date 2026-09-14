"""Create businesses, leads, prospection runs, run_leads, run_events tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create businesses table
    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("place_id", sa.String(255), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("reviews_total", sa.Integer(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("raw_provider_data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "place_id", name="uq_business_workspace_place"),
    )

    # Create prospection_runs table
    op.create_table(
        "prospection_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True),
        sa.Column("giro", sa.String(255), nullable=False),
        sa.Column("location_label", sa.String(255), nullable=False),
        sa.Column("center_latitude", sa.Float(), nullable=False),
        sa.Column("center_longitude", sa.Float(), nullable=False),
        sa.Column("radius_km", sa.Float(), nullable=True),
        sa.Column("polygon_geometry", sa.Text(), nullable=True),  # Will be Geometry in PostGIS
        sa.Column("status", sa.Enum("queued", "searching", "enriching", "deduplicating", "ready", "partial", "failed", name="prospectionrunstatus"), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(50), nullable=False, server_default="google_places"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_prospection_runs_workspace_status", "prospection_runs", ["workspace_id", "status"])
    op.create_index("ix_prospection_runs_created_by", "prospection_runs", ["created_by_id"])

    # Create leads table (pipeline_stage_id will be added in Task 11 / migration 0005)
    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prospection_runs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("setter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("closer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("profile", sa.Enum("unclassified", "oro", "agencia", "inquilino", "frio", name="leadprofile"), nullable=False, server_default="unclassified"),
        sa.Column("validation_status", sa.Enum("pending", "running", "complete", "partial", "error", name="validationstatus"), nullable=False, server_default="pending"),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("score_reason", sa.String(500), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_value", sa.Float(), nullable=True),
        sa.Column("lost_reason", sa.String(500), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("first_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "business_id", name="uq_lead_workspace_business"),
    )

    # Create run_leads table
    op.create_table(
        "run_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prospection_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("place_id", sa.String(255), nullable=False),
        sa.Column("result", sa.Enum("new", "duplicate", "updated", "failed", name="runleadresult"), nullable=False),
        sa.Column("raw_result", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "place_id", name="uq_run_lead_run_place"),
    )

    # Create run_events table
    op.create_table(
        "run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prospection_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_run_events_run_created", "run_events", ["run_id", "created_at"])

    # Enable PostGIS geometry for polygon_geometry (if not already done by extension)
    # This will be handled by the extension migration


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("run_leads")
    op.drop_table("leads")
    op.drop_table("prospection_runs")
    op.drop_table("businesses")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS runleadresult")
    op.execute("DROP TYPE IF EXISTS prospectionrunstatus")
    op.execute("DROP TYPE IF EXISTS validationstatus")
    op.execute("DROP TYPE IF EXISTS leadprofile")
