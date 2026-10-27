"""Persist asynchronous SalesPlaybook generation jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    status_enum = postgresql.ENUM(
        "queued", "running", "succeeded", "failed",
        name="salesplaybookgenerationjobstatus",
    )
    phase_enum = postgresql.ENUM(
        "preparing_documents", "analyzing_documentation", "identifying_policies",
        "building_workflow", "validating_draft", "complete",
        name="salesplaybookgenerationjobphase",
    )
    failure_enum = postgresql.ENUM(
        "provider_timeout", "provider_error", "invalid_structured_output",
        "source_normalization_error", "extraction_error", "internal_error",
        name="salesplaybookgenerationfailure",
    )
    status_enum.create(bind, checkfirst=True)
    phase_enum.create(bind, checkfirst=True)
    failure_enum.create(bind, checkfirst=True)
    status_column_enum = postgresql.ENUM(
        name="salesplaybookgenerationjobstatus", create_type=False,
    )
    phase_column_enum = postgresql.ENUM(
        name="salesplaybookgenerationjobphase", create_type=False,
    )
    failure_column_enum = postgresql.ENUM(
        name="salesplaybookgenerationfailure", create_type=False,
    )

    op.create_table(
        "sales_playbook_generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", status_column_enum, nullable=False),
        sa.Column("phase", phase_column_enum, nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("progress_message", sa.String(length=255), nullable=False),
        sa.Column("progress_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("seller_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("seller_profile_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("retry_of_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rq_job_id", sa.String(length=100), nullable=True),
        sa.Column("result_playbook_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_code", failure_column_enum, nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["seller_profile_id"], ["seller_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["retry_of_job_id"], ["sales_playbook_generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_playbook_id"], ["sales_playbooks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_sales_playbook_generation_jobs"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key",
            name="uq_sales_playbook_generation_idempotency",
        ),
    )
    op.create_index(
        "uq_sales_playbook_generation_active_fingerprint",
        "sales_playbook_generation_jobs",
        ["workspace_id", "request_fingerprint"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_sales_playbook_generation_workspace_created",
        "sales_playbook_generation_jobs",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_playbook_generation_workspace_created",
        table_name="sales_playbook_generation_jobs",
    )
    op.drop_index(
        "uq_sales_playbook_generation_active_fingerprint",
        table_name="sales_playbook_generation_jobs",
    )
    op.drop_table("sales_playbook_generation_jobs")
    postgresql.ENUM(name="salesplaybookgenerationfailure").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="salesplaybookgenerationjobphase").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="salesplaybookgenerationjobstatus").drop(op.get_bind(), checkfirst=True)
