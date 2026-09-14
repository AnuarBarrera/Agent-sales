"""Store the validation and outreach fields from Google Sheets imports."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("giro", sa.String(length=255), nullable=True))
    op.add_column("leads", sa.Column("zona", sa.String(length=255), nullable=True))
    op.add_column("leads", sa.Column("web_status", sa.String(length=30), nullable=True))
    op.add_column("leads", sa.Column("web_check_detail", sa.String(length=100), nullable=True))
    op.add_column("leads", sa.Column("google_presence", sa.String(length=50), nullable=True))
    op.add_column("leads", sa.Column("meta_ads_active", sa.Boolean(), nullable=True))
    op.add_column("leads", sa.Column("contacted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("leads", sa.Column("source_searched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("outreach_message", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("outreach_message_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("outreach_message_error", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("raw_import_data", postgresql.JSONB(), nullable=True))
    op.create_index("ix_leads_giro", "leads", ["giro"])
    op.create_index("ix_leads_zona", "leads", ["zona"])
    op.alter_column("leads", "contacted", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_leads_zona", table_name="leads")
    op.drop_index("ix_leads_giro", table_name="leads")
    op.drop_column("leads", "raw_import_data")
    op.drop_column("leads", "outreach_message_error")
    op.drop_column("leads", "outreach_message_generated_at")
    op.drop_column("leads", "outreach_message")
    op.drop_column("leads", "source_searched_at")
    op.drop_column("leads", "contacted")
    op.drop_column("leads", "meta_ads_active")
    op.drop_column("leads", "google_presence")
    op.drop_column("leads", "web_check_detail")
    op.drop_column("leads", "web_status")
    op.drop_column("leads", "zona")
    op.drop_column("leads", "giro")
