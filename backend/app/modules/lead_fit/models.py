"""Tenant-scoped persistence contracts for Lead Fit / Jev."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
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

from app.db.base import Base, enum_values


class LeadFitPolicyStatus(str, PyEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class LeadFitEvaluationStatus(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class LeadFitClassification(str, PyEnum):
    QUALIFIED = "qualified"
    NEEDS_MORE_RESEARCH = "needs_more_research"
    NOT_QUALIFIED = "not_qualified"


class LeadFitResearchGrantStatus(str, PyEnum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"


def _string_enum(enum_class: type[PyEnum], name: str, length: int) -> Enum:
    return Enum(
        enum_class,
        values_callable=enum_values,
        name=name,
        length=length,
        native_enum=False,
        create_constraint=False,
    )


class LeadFitPolicy(Base):
    """Approved policy snapshot; migration guards authority fields as immutable."""

    __tablename__ = "lead_fit_policies"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    commercial_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    status: Mapped[LeadFitPolicyStatus] = mapped_column(
        _string_enum(LeadFitPolicyStatus, "leadfitpolicystatus", 20),
        default=LeadFitPolicyStatus.APPROVED,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'approved', 'superseded')", name="status"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("commercial_digest ~ '^[0-9a-f]{64}$'", name="commercial_digest_sha256"),
        CheckConstraint("policy_digest ~ '^[0-9a-f]{64}$'", name="policy_digest_sha256"),
        UniqueConstraint("workspace_id", "id", name="uq_lead_fit_policies_workspace_id"),
        UniqueConstraint(
            "workspace_id", "playbook_id", "version", name="uq_lead_fit_policies_workspace_playbook_version"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "playbook_id"], ["sales_playbooks.workspace_id", "sales_playbooks.id"],
            name="fk_lead_fit_policies_playbook_tenant", ondelete="RESTRICT",
        ),
        Index("ix_lead_fit_policies_workspace_approved", "workspace_id", "status"),
    )


class LeadFitEvaluation(Base):
    """Durable evaluation request, dispatch audit and result contract."""

    __tablename__ = "lead_fit_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    research_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    previous_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    question_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(255), nullable=False)
    response_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[LeadFitEvaluationStatus] = mapped_column(
        _string_enum(LeadFitEvaluationStatus, "leadfitevaluationstatus", 20),
        default=LeadFitEvaluationStatus.PENDING,
        nullable=False,
        index=True,
    )
    classification: Mapped[LeadFitClassification | None] = mapped_column(
        _string_enum(LeadFitClassification, "leadfitclassification", 30), nullable=True
    )
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage: Mapped[float | None] = mapped_column(nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    audit: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    budget_reserved_usd: Mapped[float | None] = mapped_column(nullable=True)
    budget_consumed_usd: Mapped[float | None] = mapped_column(nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')", name="status"),
        CheckConstraint(
            "classification IS NULL OR status = 'completed'",
            name="classification_only_completed",
        ),
        CheckConstraint(
            "status != 'completed' OR classification IS NOT NULL",
            name="completed_requires_classification",
        ),
        CheckConstraint(
            "classification IS NULL OR classification IN ('qualified', 'needs_more_research', 'not_qualified')",
            name="classification_domain",
        ),
        CheckConstraint("fit_score IS NULL OR (fit_score >= 0 AND fit_score <= 100)", name="fit_score_range"),
        CheckConstraint("coverage IS NULL OR (coverage >= 0 AND coverage <= 100)", name="coverage_range"),
        CheckConstraint("call_count >= 0", name="call_count_nonnegative"),
        CheckConstraint("call_count <= 2", name="call_count_limit"),
        CheckConstraint("budget_reserved_usd IS NULL OR budget_reserved_usd >= 0", name="reserved_nonnegative"),
        CheckConstraint("budget_consumed_usd IS NULL OR budget_consumed_usd >= 0", name="consumed_nonnegative"),
        CheckConstraint("policy_digest ~ '^[0-9a-f]{64}$'", name="policy_digest_sha256"),
        CheckConstraint("source_digest ~ '^[0-9a-f]{64}$'", name="source_digest_sha256"),
        CheckConstraint("question_digest IS NULL OR question_digest ~ '^[0-9a-f]{64}$'", name="question_digest_sha256"),
        CheckConstraint("result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'", name="result_digest_sha256"),
        UniqueConstraint("workspace_id", "id", name="uq_lead_fit_evaluations_workspace_id"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_lead_fit_evaluations_workspace_idempotency"),
        ForeignKeyConstraint(
            ["workspace_id", "lead_id"], ["leads.workspace_id", "leads.id"],
            name="fk_lead_fit_evaluations_lead_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "policy_id"], ["lead_fit_policies.workspace_id", "lead_fit_policies.id"],
            name="fk_lead_fit_evaluations_policy_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "research_id"], ["prospect_research.workspace_id", "prospect_research.id"],
            name="fk_lead_fit_evaluations_research_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "previous_id"], ["lead_fit_evaluations.workspace_id", "lead_fit_evaluations.id"],
            name="fk_lead_fit_evaluations_previous_tenant", ondelete="RESTRICT",
        ),
        Index("ix_lead_fit_evaluations_workspace_lead_created", "workspace_id", "lead_id", "requested_at"),
    )


class LeadFitResearchGrant(Base):
    """Human-authorized, bounded incremental research record."""

    __tablename__ = "lead_fit_research_grants"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    max_search: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_extract: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_jev: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    monetary_cap_usd: Mapped[float | None] = mapped_column(nullable=True)
    consumed_search: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed_extract: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed_jev: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_search: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_extract: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_jev: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed_usd: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[LeadFitResearchGrantStatus] = mapped_column(
        _string_enum(LeadFitResearchGrantStatus, "leadfitresearchgrantstatus", 20),
        default=LeadFitResearchGrantStatus.PENDING,
        nullable=False,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unchanged_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'authorized', 'running', 'completed', 'failed', 'exhausted')", name="status"),
        CheckConstraint("source_digest ~ '^[0-9a-f]{64}$'", name="source_digest_sha256"),
        CheckConstraint("plan_hash ~ '^[0-9a-f]{64}$'", name="plan_hash_sha256"),
        CheckConstraint(
            "max_search BETWEEN 0 AND 1 AND max_extract BETWEEN 0 AND 1 "
            "AND max_jev BETWEEN 0 AND 2 AND max_rounds BETWEEN 0 AND 1",
            name="max_limits",
        ),
        CheckConstraint("consumed_search >= 0 AND consumed_extract >= 0 AND consumed_jev >= 0", name="consumed_nonnegative"),
        CheckConstraint("reserved_search >= 0 AND reserved_extract >= 0 AND reserved_jev >= 0", name="reserved_nonnegative"),
        CheckConstraint(
            "reserved_search <= max_search AND reserved_extract <= max_extract "
            "AND reserved_jev <= max_jev",
            name="reserved_within_max",
        ),
        CheckConstraint(
            "consumed_search <= reserved_search AND consumed_extract <= reserved_extract "
            "AND consumed_jev <= reserved_jev",
            name="consumed_within_reserved",
        ),
        CheckConstraint("unchanged_rounds >= 0", name="unchanged_rounds_nonnegative"),
        CheckConstraint("monetary_cap_usd IS NULL OR monetary_cap_usd >= 0", name="cap_nonnegative"),
        CheckConstraint("consumed_usd IS NULL OR consumed_usd >= 0", name="consumed_usd_nonnegative"),
        UniqueConstraint("workspace_id", "id", name="uq_lead_fit_research_grants_workspace_id"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_lead_fit_research_grants_workspace_idempotency"),
        ForeignKeyConstraint(
            ["workspace_id", "lead_id"], ["leads.workspace_id", "leads.id"],
            name="fk_lead_fit_research_grants_lead_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evaluation_id"], ["lead_fit_evaluations.workspace_id", "lead_fit_evaluations.id"],
            name="fk_lead_fit_research_grants_evaluation_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "policy_id"], ["lead_fit_policies.workspace_id", "lead_fit_policies.id"],
            name="fk_lead_fit_research_grants_policy_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "result_evaluation_id"], ["lead_fit_evaluations.workspace_id", "lead_fit_evaluations.id"],
            name="fk_lead_fit_research_grants_result_evaluation_tenant", ondelete="RESTRICT",
        ),
        Index("ix_lead_fit_research_grants_workspace_lead", "workspace_id", "lead_id"),
    )
