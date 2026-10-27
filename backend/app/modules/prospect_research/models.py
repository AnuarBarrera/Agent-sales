import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values


class ProspectResearchStatus(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ProspectResearch(Base):
    """One bounded, auditable public-web research operation for a lead."""

    __tablename__ = "prospect_research"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_of_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prospect_research.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ProspectResearchStatus] = mapped_column(
        Enum(ProspectResearchStatus, values_callable=enum_values, name="prospectresearchstatus"),
        default=ProspectResearchStatus.PENDING,
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), default="tavily", nullable=False)
    operation: Mapped[str] = mapped_column(String(50), default="search_extract", nullable=False)
    query_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    query_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extracted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    search_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extract_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    evidence: Mapped[list["ResearchEvidence"]] = relationship(
        back_populates="research", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_prospect_research_workspace_id"),
        Index("ix_prospect_research_workspace_lead_created", "workspace_id", "lead_id", "created_at"),
    )


class ResearchEvidence(Base):
    """Original provider material, never an LLM-generated summary."""

    __tablename__ = "research_evidence"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    research_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prospect_research.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), default="tavily", nullable=False)
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    research: Mapped[ProspectResearch] = relationship(back_populates="evidence")

    __table_args__ = (
        Index("ix_research_evidence_workspace_lead_retrieved", "workspace_id", "lead_id", "retrieved_at"),
    )
