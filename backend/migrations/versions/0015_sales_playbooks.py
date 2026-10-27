"""Create structured, versioned sales playbooks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    status_enum = postgresql.ENUM(
        "draft", "approved", "archived", name="salesplaybookstatus", create_type=False
    )
    source_enum = postgresql.ENUM(
        "questionnaire", "document", "manual", name="salesplaybooksourcetype", create_type=False
    )
    status_enum.create(bind, checkfirst=True)
    source_enum.create(bind, checkfirst=True)

    op.create_table(
        "sales_playbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seller_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="draft"),
        sa.Column("source_type", source_enum, nullable=False),
        sa.Column("structured_content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seller_profile_id"], ["seller_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "version", name="uq_sales_playbook_workspace_version"),
    )
    op.create_index("ix_sales_playbooks_workspace_id", "sales_playbooks", ["workspace_id"])
    op.create_index("ix_sales_playbooks_seller_profile_id", "sales_playbooks", ["seller_profile_id"])
    op.create_index("ix_sales_playbooks_status", "sales_playbooks", ["status"])
    op.create_index(
        "uq_sales_playbook_workspace_approved",
        "sales_playbooks",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )


def downgrade() -> None:
    op.drop_index("uq_sales_playbook_workspace_approved", table_name="sales_playbooks")
    op.drop_index("ix_sales_playbooks_status", table_name="sales_playbooks")
    op.drop_index("ix_sales_playbooks_seller_profile_id", table_name="sales_playbooks")
    op.drop_index("ix_sales_playbooks_workspace_id", table_name="sales_playbooks")
    op.drop_table("sales_playbooks")
    op.execute("DROP TYPE IF EXISTS salesplaybooksourcetype")
    op.execute("DROP TYPE IF EXISTS salesplaybookstatus")
