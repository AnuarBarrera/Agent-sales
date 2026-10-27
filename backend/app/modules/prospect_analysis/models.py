import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values


class ProspectAnalysisStatus(str, PyEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class ProspectAnalysis(Base):
    """Versioned intelligence + qualification generated from one evidence snapshot."""

    __tablename__ = "prospect_analyses"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sales_playbook_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    research_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    previous_analysis_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    fit_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    status: Mapped[ProspectAnalysisStatus] = mapped_column(
        Enum(ProspectAnalysisStatus, values_callable=enum_values, name="prospectanalysisstatus"),
        nullable=False,
        index=True,
    )
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    intelligence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    qualification: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    intelligence_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    intelligence_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intelligence_prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    intelligence_contract_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    intelligence_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intelligence_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qualification_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qualification_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qualification_prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qualification_contract_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    qualification_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qualification_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_prospect_analyses_workspace_id"),
        Index("ix_prospect_analyses_workspace_lead_created", "workspace_id", "lead_id", "created_at"),
        ForeignKeyConstraint(
            ["workspace_id", "lead_id"], ["leads.workspace_id", "leads.id"],
            name="fk_prospect_analyses_lead_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "sales_playbook_id"], ["sales_playbooks.workspace_id", "sales_playbooks.id"],
            name="fk_prospect_analyses_playbook_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "research_id"], ["prospect_research.workspace_id", "prospect_research.id"],
            name="fk_prospect_analyses_research_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "previous_analysis_id"], ["prospect_analyses.workspace_id", "prospect_analyses.id"],
            name="fk_prospect_analyses_previous_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "fit_evaluation_id"], ["lead_fit_evaluations.workspace_id", "lead_fit_evaluations.id"],
            name="fk_prospect_analyses_fit_evaluation_tenant", ondelete="RESTRICT",
        ),
    )
