import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values


class ValidationCheckType(str, PyEnum):
    WEBSITE = "website"
    GOOGLE_PRESENCE = "google_presence"
    META_ADS = "meta_ads"


class ValidationResult(str, PyEnum):
    VALID = "valid"
    INVALID = "invalid"
    ACTIVE = "active"
    NONE = "none"
    NOT_FOUND = "not_found"
    PENDING = "pending"
    ERROR = "error"


class LeadValidation(Base):
    __tablename__ = "lead_validations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prospection_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    check_type: Mapped[ValidationCheckType] = mapped_column(Enum(ValidationCheckType, values_callable=enum_values), nullable=False)
    result: Mapped[ValidationResult] = mapped_column(Enum(ValidationResult, values_callable=enum_values), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    attempt: Mapped[int] = mapped_column(default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint("lead_id", "check_type", "attempt", name="uq_lead_validation_lead_check_attempt"),
        Index("ix_lead_validations_lead_check", "lead_id", "check_type"),
    )


class LeadProfile(str, PyEnum):
    UNCLASSIFIED = "unclassified"
    ORO = "oro"
    AGENCIA = "agencia"
    INQUILINO = "inquilino"
    FRIO = "frio"


class ValidationSnapshot(Base):
    __tablename__ = "validation_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    website_result: Mapped[ValidationResult | None] = mapped_column(Enum(ValidationResult, values_callable=enum_values), nullable=True)
    google_presence_result: Mapped[ValidationResult | None] = mapped_column(Enum(ValidationResult, values_callable=enum_values), nullable=True)
    meta_ads_result: Mapped[ValidationResult | None] = mapped_column(Enum(ValidationResult, values_callable=enum_values), nullable=True)
    profile: Mapped[LeadProfile | None] = mapped_column(Enum(LeadProfile, values_callable=enum_values), nullable=True)
    score: Mapped[int | None] = mapped_column(nullable=True)
    score_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (
        UniqueConstraint("lead_id", name="uq_validation_snapshot_lead"),
    )
