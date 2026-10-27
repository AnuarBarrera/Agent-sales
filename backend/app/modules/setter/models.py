"""Persisted, review-only Setter execution decisions and drafts."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SetterExecution(Base):
    """One idempotent decision based on a fixed set of approved CRM snapshots."""

    __tablename__ = "setter_executions"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    analysis_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    research_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    playbook_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    playbook_version: Mapped[str] = mapped_column(String(100), nullable=False)
    playbook_digest: Mapped[str] = mapped_column(String(256), nullable=False)
    workflow_stage: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(50), default="pending_human_review", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "lead_id"],
            ["leads.workspace_id", "leads.id"],
            name="fk_setter_executions_workspace_id_leads",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "analysis_id"],
            ["prospect_analyses.workspace_id", "prospect_analyses.id"],
            name="fk_setter_executions_workspace_id_prospect_analyses",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "research_id"],
            ["prospect_research.workspace_id", "prospect_research.id"],
            name="fk_setter_executions_workspace_id_prospect_research",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "playbook_id"],
            ["sales_playbooks.workspace_id", "sales_playbooks.id"],
            name="fk_setter_executions_workspace_id_sales_playbooks",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("idempotency_key", name="uq_setter_executions_idempotency_key"),
        CheckConstraint(
            "(provider IS NULL AND model IS NULL) OR (provider IS NOT NULL AND model IS NOT NULL)",
            name="provider_model_pair",
        ),
        Index("ix_setter_executions_workspace_lead_created", "workspace_id", "lead_id", "created_at"),
    )
