"""Add tenant-scoped Tavily prospect research and source evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    research_status = postgresql.ENUM(
        "pending", "running", "completed", "failed", name="prospectresearchstatus", create_type=False
    )
    research_status.create(bind, checkfirst=True)
    op.create_table(
        "prospect_research",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_of_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", research_status, nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="tavily"),
        sa.Column("operation", sa.String(length=50), nullable=False, server_default="search_extract"),
        sa.Column("query_summary", sa.String(length=500), nullable=False),
        sa.Column("query_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_credits", sa.Integer(), nullable=True),
        sa.Column("extract_credits", sa.Integer(), nullable=True),
        sa.Column("total_credits", sa.Integer(), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["refresh_of_id"], ["prospect_research.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prospect_research_workspace_id", "prospect_research", ["workspace_id"])
    op.create_index("ix_prospect_research_lead_id", "prospect_research", ["lead_id"])
    op.create_index("ix_prospect_research_status", "prospect_research", ["status"])
    op.create_index(
        "ix_prospect_research_workspace_lead_created",
        "prospect_research",
        ["workspace_id", "lead_id", "created_at"],
    )

    op.create_table(
        "research_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="tavily"),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_title", sa.String(length=500), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_id"], ["prospect_research.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id", name="uq_research_evidence_evidence_id"),
    )
    op.create_index("ix_research_evidence_evidence_id", "research_evidence", ["evidence_id"])
    op.create_index("ix_research_evidence_workspace_id", "research_evidence", ["workspace_id"])
    op.create_index("ix_research_evidence_research_id", "research_evidence", ["research_id"])
    op.create_index("ix_research_evidence_lead_id", "research_evidence", ["lead_id"])
    op.create_index(
        "ix_research_evidence_workspace_lead_retrieved",
        "research_evidence",
        ["workspace_id", "lead_id", "retrieved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_evidence_workspace_lead_retrieved", table_name="research_evidence")
    op.drop_index("ix_research_evidence_lead_id", table_name="research_evidence")
    op.drop_index("ix_research_evidence_research_id", table_name="research_evidence")
    op.drop_index("ix_research_evidence_workspace_id", table_name="research_evidence")
    op.drop_index("ix_research_evidence_evidence_id", table_name="research_evidence")
    op.drop_table("research_evidence")
    op.drop_index("ix_prospect_research_workspace_lead_created", table_name="prospect_research")
    op.drop_index("ix_prospect_research_status", table_name="prospect_research")
    op.drop_index("ix_prospect_research_lead_id", table_name="prospect_research")
    op.drop_index("ix_prospect_research_workspace_id", table_name="prospect_research")
    op.drop_table("prospect_research")
    op.execute("DROP TYPE IF EXISTS prospectresearchstatus")
