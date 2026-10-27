import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values


class SalesPlaybookStatus(str, PyEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class SalesPlaybookSourceType(str, PyEnum):
    QUESTIONNAIRE = "questionnaire"
    DOCUMENT = "document"
    MANUAL = "manual"


class SalesPlaybook(Base):
    """A versioned commercial strategy derived for one sales workspace."""

    __tablename__ = "sales_playbooks"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seller_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("seller_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SalesPlaybookStatus] = mapped_column(
        Enum(SalesPlaybookStatus, values_callable=enum_values, name="salesplaybookstatus"),
        default=SalesPlaybookStatus.DRAFT,
        nullable=False,
        index=True,
    )
    source_type: Mapped[SalesPlaybookSourceType] = mapped_column(
        Enum(SalesPlaybookSourceType, values_callable=enum_values, name="salesplaybooksourcetype"),
        nullable=False,
    )
    structured_content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Null marks legacy Block 2 content. Legacy rows remain auditable but are not
    # eligible as executable strategy context for later agent workflows.
    content_contract_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_operation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    generation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_audit: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(5000), nullable=True)
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
        UniqueConstraint("workspace_id", "id", name="uq_sales_playbooks_workspace_id"),
        UniqueConstraint("workspace_id", "version", name="uq_sales_playbook_workspace_version"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_sales_playbook_workspace_idempotency_key"),
        Index(
            "uq_sales_playbook_workspace_approved",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'approved'"),
        ),
    )
