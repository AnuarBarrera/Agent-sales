"""Persist immutable uploaded and normalized SalesPlaybook sources."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_playbook_source_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=180), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("original_size_bytes", sa.Integer(), nullable=False),
        sa.Column("original_object_key", sa.String(length=512), nullable=False),
        sa.Column("original_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_format", sa.String(length=40), nullable=False),
        sa.Column("normalized_object_key", sa.String(length=512), nullable=False),
        sa.Column("normalized_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalization_status", sa.String(length=30), nullable=False),
        sa.Column("normalization_warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("converter", sa.String(length=80), nullable=False),
        sa.Column("converter_version", sa.String(length=100), nullable=False),
        sa.Column("sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_playbook_source_documents")),
        sa.UniqueConstraint("workspace_id", "id", name="uq_sales_playbook_source_workspace_id"),
    )

def downgrade() -> None:
    op.drop_table("sales_playbook_source_documents")
