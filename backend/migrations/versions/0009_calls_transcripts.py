"""Create calls, transcripts, and RAG tables

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-14

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types using DO blocks (PostgreSQL compatible)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE callstatus AS ENUM ('uploaded', 'transcribing', 'transcribed', 'failed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE filetype AS ENUM ('audio', 'transcript', 'document');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Create call_records table - reference existing enum types directly
    op.create_table(
        "call_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("uploaded_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", postgresql.ENUM(name="callstatus", create_type=False), nullable=False, server_default="uploaded"),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("call_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_call_records_lead_created", "call_records", ["lead_id", "created_at"])

    # Create file_assets table
    op.create_table(
        "file_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("call_record_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("call_records.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("uploaded_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("file_type", postgresql.ENUM(name="filetype", create_type=False), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Create transcripts table
    op.create_table(
        "transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("call_record_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("call_records.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("file_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("file_assets.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status", postgresql.ENUM(name="callstatus", create_type=False), nullable=False, server_default="uploaded"),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("provider_request_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create transcript_analyses table
    op.create_table(
        "transcript_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("intent", sa.String(255), nullable=True),
        sa.Column("objections", sa.Text(), nullable=True),
        sa.Column("needs", sa.Text(), nullable=True),
        sa.Column("next_steps", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create document_chunks table for RAG
    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),  # Using text for now, will be vector
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_document_chunks_source", "document_chunks", ["source_type", "source_id"])


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("transcript_analyses")
    op.drop_table("transcripts")
    op.drop_table("file_assets")
    op.drop_table("call_records")
    # Don't drop enum types as they may be used by other tables
