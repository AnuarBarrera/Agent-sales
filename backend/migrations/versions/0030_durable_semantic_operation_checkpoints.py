"""Persist validated generic Semantic IR operation checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_playbook_generation_jobs",
        sa.Column("resume_from_generation_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_generation_job_resume_source",
        "sales_playbook_generation_jobs",
        "sales_playbook_generation_jobs",
        ["resume_from_generation_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_sales_playbook_generation_resume_source",
        "sales_playbook_generation_jobs",
        ["resume_from_generation_job_id"],
    )

    op.create_table(
        "sales_playbook_semantic_operation_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", sa.String(length=180), nullable=False),
        sa.Column("operation_identity_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("operation_order", sa.Integer(), nullable=False),
        sa.Column("checkpoint_format", sa.String(length=64), nullable=False),
        sa.Column("semantic_contract_revision", sa.String(length=80), nullable=False),
        sa.Column("partition_plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("chunk_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("workspace_context_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_document_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("seller_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("seller_profile_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("operation_version", sa.String(length=80), nullable=False),
        sa.Column("provider_name", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("prompt_revision", sa.String(length=120), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("generation_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generation_config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("dependency_fingerprints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validated_ir", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("validation_status", sa.String(length=16), server_default="validated", nullable=False),
        sa.Column("origin_checkpoint_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "operation_order >= 0",
            name="ck_sales_playbook_semantic_operation_checkpoints_semantic_checkpoint_nonnegative_order",
        ),
        sa.CheckConstraint(
            "validation_status = 'validated'",
            name="ck_sales_playbook_semantic_operation_checkpoints_semantic_checkpoint_validated_only",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validated_ir) = 'object'",
            name="ck_sales_playbook_semantic_operation_checkpoints_semantic_checkpoint_ir_object",
        ),
        sa.CheckConstraint(
            "operation_identity_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "partition_plan_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "chunk_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "input_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "workspace_context_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "source_content_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "schema_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "generation_config_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "output_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "(seller_profile_fingerprint IS NULL OR seller_profile_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_sales_playbook_semantic_operation_checkpoints_semantic_checkpoint_sha256_fingerprints",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_job_id"], ["sales_playbook_generation_jobs.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["origin_checkpoint_id"],
            ["sales_playbook_semantic_operation_checkpoints.id"],
            name="fk_semantic_checkpoint_origin",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sales_playbook_semantic_operation_checkpoints"),
        sa.UniqueConstraint(
            "workspace_id", "generation_job_id", "operation_id",
            name="uq_sales_playbook_semantic_checkpoint_operation",
        ),
    )
    op.create_index(
        "ix_sales_playbook_semantic_checkpoint_resume",
        "sales_playbook_semantic_operation_checkpoints",
        ["workspace_id", "generation_job_id", "operation_order"],
    )
    op.execute("""
        CREATE FUNCTION reject_semantic_checkpoint_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'semantic operation checkpoints are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_semantic_checkpoint_immutable
        BEFORE UPDATE ON sales_playbook_semantic_operation_checkpoints
        FOR EACH ROW EXECUTE FUNCTION reject_semantic_checkpoint_update()
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_semantic_checkpoint_immutable "
        "ON sales_playbook_semantic_operation_checkpoints"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_semantic_checkpoint_update()")
    op.drop_index(
        "ix_sales_playbook_semantic_checkpoint_resume",
        table_name="sales_playbook_semantic_operation_checkpoints",
    )
    op.drop_table("sales_playbook_semantic_operation_checkpoints")
    op.drop_index(
        "ix_sales_playbook_generation_resume_source",
        table_name="sales_playbook_generation_jobs",
    )
    op.drop_constraint(
        "fk_generation_job_resume_source",
        "sales_playbook_generation_jobs",
        type_="foreignkey",
    )
    op.drop_column("sales_playbook_generation_jobs", "resume_from_generation_job_id")
