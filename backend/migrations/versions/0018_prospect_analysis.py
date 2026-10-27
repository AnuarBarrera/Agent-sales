"""Add versioned prospect intelligence and qualification analyses."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    status_enum = postgresql.ENUM(
        "completed", "failed", name="prospectanalysisstatus", create_type=False
    )
    status_enum.create(bind, checkfirst=True)
    op.create_table(
        "prospect_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sales_playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("intelligence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("qualification", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("intelligence_provider", sa.String(length=100), nullable=True),
        sa.Column("intelligence_model", sa.String(length=255), nullable=True),
        sa.Column("intelligence_prompt_version", sa.String(length=100), nullable=True),
        sa.Column("intelligence_contract_version", sa.String(length=20), nullable=True),
        sa.Column("intelligence_latency_ms", sa.Integer(), nullable=True),
        sa.Column("intelligence_tokens", sa.Integer(), nullable=True),
        sa.Column("qualification_provider", sa.String(length=100), nullable=True),
        sa.Column("qualification_model", sa.String(length=255), nullable=True),
        sa.Column("qualification_prompt_version", sa.String(length=100), nullable=True),
        sa.Column("qualification_contract_version", sa.String(length=20), nullable=True),
        sa.Column("qualification_latency_ms", sa.Integer(), nullable=True),
        sa.Column("qualification_tokens", sa.Integer(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sales_playbook_id"], ["sales_playbooks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["research_id"], ["prospect_research.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_analysis_id"], ["prospect_analyses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prospect_analyses_workspace_id", "prospect_analyses", ["workspace_id"])
    op.create_index("ix_prospect_analyses_lead_id", "prospect_analyses", ["lead_id"])
    op.create_index("ix_prospect_analyses_sales_playbook_id", "prospect_analyses", ["sales_playbook_id"])
    op.create_index("ix_prospect_analyses_research_id", "prospect_analyses", ["research_id"])
    op.create_index("ix_prospect_analyses_status", "prospect_analyses", ["status"])
    op.create_index(
        "ix_prospect_analyses_workspace_lead_created",
        "prospect_analyses",
        ["workspace_id", "lead_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_prospect_analyses_workspace_lead_created", table_name="prospect_analyses")
    op.drop_index("ix_prospect_analyses_status", table_name="prospect_analyses")
    op.drop_index("ix_prospect_analyses_research_id", table_name="prospect_analyses")
    op.drop_index("ix_prospect_analyses_sales_playbook_id", table_name="prospect_analyses")
    op.drop_index("ix_prospect_analyses_lead_id", table_name="prospect_analyses")
    op.drop_index("ix_prospect_analyses_workspace_id", table_name="prospect_analyses")
    op.drop_table("prospect_analyses")
    op.execute("DROP TYPE IF EXISTS prospectanalysisstatus")
