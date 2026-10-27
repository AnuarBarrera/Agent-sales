"""Add durable OTP, participant, and demo admission records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_key", sa.String(254), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column("search_limit", sa.Integer(), server_default="2", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("search_limit = 2", name="ck_demo_participants_search_limit"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_participants"),
        sa.UniqueConstraint("email_key", name="uq_demo_participants_email_key"),
        sa.UniqueConstraint("user_id", name="uq_demo_participants_user_id"),
        sa.UniqueConstraint("workspace_id", name="uq_demo_participants_workspace_id"),
    )
    op.create_table(
        "demo_otp_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_key", sa.String(254), nullable=False),
        sa.Column("code_hmac", sa.LargeBinary(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_demo_otp_attempts"),
        sa.CheckConstraint("delivery_status IN ('pending', 'sent', 'failed')", name="ck_demo_otp_delivery_status"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_otp_challenges"),
    )
    op.create_index("ix_demo_otp_challenges_email_key", "demo_otp_challenges", ["email_key"])
    op.create_table(
        "demo_search_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("request_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["participant_id"], ["demo_participants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["prospection_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_search_reservations"),
        sa.UniqueConstraint("participant_id", "key_hash", name="uq_demo_search_participant_key"),
        sa.UniqueConstraint("run_id", name="uq_demo_search_run_id"),
    )
    op.create_index("ix_demo_search_reservations_participant_id", "demo_search_reservations", ["participant_id"])
    op.create_table(
        "demo_draft_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("request_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="reserved", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("channel IN ('email', 'phone')", name="ck_demo_draft_channel"),
        sa.CheckConstraint("status IN ('reserved', 'dispatched', 'failed')", name="ck_demo_draft_status"),
        sa.ForeignKeyConstraint(["participant_id"], ["demo_participants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["execution_id"], ["setter_executions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_draft_reservations"),
        sa.UniqueConstraint("participant_id", "lead_id", name="uq_demo_draft_participant_lead"),
    )
    op.create_index("ix_demo_draft_reservations_participant_id", "demo_draft_reservations", ["participant_id"])


def downgrade() -> None:
    op.drop_index("ix_demo_draft_reservations_participant_id", table_name="demo_draft_reservations")
    op.drop_table("demo_draft_reservations")
    op.drop_index("ix_demo_search_reservations_participant_id", table_name="demo_search_reservations")
    op.drop_table("demo_search_reservations")
    op.drop_index("ix_demo_otp_challenges_email_key", table_name="demo_otp_challenges")
    op.drop_table("demo_otp_challenges")
    op.drop_table("demo_participants")
