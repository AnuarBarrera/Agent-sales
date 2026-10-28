"""Reserve at most two single-call Nemotron suggestions per participant."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "demo_ai_suggestions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("participant_id", pg.UUID(as_uuid=True), sa.ForeignKey("demo_participants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lead_id", pg.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_snapshot", pg.JSONB(), nullable=False),
        sa.Column("content", pg.JSONB()),
        sa.Column("generation_audit", pg.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("participant_id", "lead_id", name="uq_demo_ai_participant_lead"),
        sa.UniqueConstraint("participant_id", "slot", name="uq_demo_ai_participant_slot"),
        sa.CheckConstraint("slot IN (1, 2)", name="ck_demo_ai_slot"),
        sa.CheckConstraint("status IN ('reserved', 'completed', 'failed')", name="ck_demo_ai_status"),
    )


def downgrade():
    op.drop_table("demo_ai_suggestions")
