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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values


class GenerationJobStatus(str, PyEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GenerationJobPhase(str, PyEnum):
    PREPARING_DOCUMENTS = "preparing_documents"
    ANALYZING_DOCUMENTATION = "analyzing_documentation"
    IDENTIFYING_POLICIES = "identifying_policies"
    BUILDING_WORKFLOW = "building_workflow"
    VALIDATING_DRAFT = "validating_draft"
    COMPLETE = "complete"


class GenerationFailureCode(str, PyEnum):
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    SOURCE_NORMALIZATION_ERROR = "source_normalization_error"
    EXTRACTION_ERROR = "extraction_error"
    EXTRACTION_CONTRACT_ERROR = "extraction_contract_error"
    PROVENANCE_VALIDATION_ERROR = "provenance_validation_error"
    PLAYBOOK_ASSEMBLY_VALIDATION_ERROR = "playbook_assembly_validation_error"
    INTERNAL_ERROR = "internal_error"


class SalesPlaybookGenerationJob(Base):
    """Durable tenant-scoped state for one asynchronous playbook generation."""

    __tablename__ = "sales_playbook_generation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    status: Mapped[GenerationJobStatus] = mapped_column(
        Enum(GenerationJobStatus, values_callable=enum_values, name="salesplaybookgenerationjobstatus"),
        nullable=False, default=GenerationJobStatus.QUEUED,
    )
    phase: Mapped[GenerationJobPhase] = mapped_column(
        Enum(GenerationJobPhase, values_callable=enum_values, name="salesplaybookgenerationjobphase"),
        nullable=False, default=GenerationJobPhase.PREPARING_DOCUMENTS,
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str] = mapped_column(String(255), nullable=False, default="Preparando documentación")
    progress_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    seller_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("seller_profiles.id", ondelete="SET NULL"), nullable=True,
    )
    seller_profile_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_playbook_generation_jobs.id", ondelete="SET NULL"), nullable=True,
    )
    resume_from_generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sales_playbook_generation_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    rq_job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result_playbook_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales_playbooks.id", ondelete="SET NULL"), nullable=True,
    )
    failure_code: Mapped[GenerationFailureCode | None] = mapped_column(
        Enum(GenerationFailureCode, values_callable=enum_values, name="salesplaybookgenerationfailure"),
        nullable=True,
    )
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_sales_playbook_generation_idempotency"),
        Index(
            "uq_sales_playbook_generation_active_fingerprint",
            "workspace_id", "request_fingerprint", unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_sales_playbook_generation_workspace_created", "workspace_id", "created_at"),
        Index("ix_sales_playbook_generation_resume_source", "resume_from_generation_job_id"),
    )


class SemanticOperationCheckpoint(Base):
    """Immutable, validated Semantic IR output for one operation in one job."""

    __tablename__ = "sales_playbook_semantic_operation_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sales_playbook_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_id: Mapped[str] = mapped_column(String(180), nullable=False)
    operation_identity_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_order: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_format: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_contract_revision: Mapped[str] = mapped_column(String(80), nullable=False)
    partition_plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_document_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source_manifest: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source_content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    seller_profile_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    seller_profile_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation_version: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_fingerprints: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    validated_ir: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_provenance: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    output_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="validated")
    origin_checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sales_playbook_semantic_operation_checkpoints.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "generation_job_id", "operation_id",
            name="uq_sales_playbook_semantic_checkpoint_operation",
        ),
        CheckConstraint("operation_order >= 0", name="semantic_checkpoint_nonnegative_order"),
        CheckConstraint("validation_status = 'validated'", name="semantic_checkpoint_validated_only"),
        CheckConstraint("jsonb_typeof(validated_ir) = 'object'", name="semantic_checkpoint_ir_object"),
        CheckConstraint(
            "operation_identity_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "partition_plan_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "chunk_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "input_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "workspace_context_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "source_content_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "schema_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "generation_config_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "output_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "(seller_profile_fingerprint IS NULL OR seller_profile_fingerprint ~ '^[0-9a-f]{64}$')",
            name="semantic_checkpoint_sha256_fingerprints",
        ),
        Index(
            "ix_sales_playbook_semantic_checkpoint_resume",
            "workspace_id", "generation_job_id", "operation_order",
        ),
    )
