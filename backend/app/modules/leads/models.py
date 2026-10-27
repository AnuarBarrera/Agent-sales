import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values

if TYPE_CHECKING:
    from app.modules.identity.models import User
    from app.modules.outreach.models import Conversation
    from app.modules.pipeline.models import PipelineStage


class LeadProfile(str, PyEnum):
    UNCLASSIFIED = "unclassified"
    ORO = "oro"
    AGENCIA = "agencia"
    INQUILINO = "inquilino"
    FRIO = "frio"


class ValidationStatus(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    ERROR = "error"


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    place_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rating: Mapped[float | None] = mapped_column(nullable=True)
    reviews_total: Mapped[int | None] = mapped_column(nullable=True)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)
    raw_provider_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    leads: Mapped[list["Lead"]] = relationship(
        back_populates="business",
        cascade="all, delete-orphan",
        primaryjoin="and_(Business.id == Lead.business_id, Business.workspace_id == Lead.workspace_id)",
        foreign_keys="[Lead.business_id, Lead.workspace_id]",
    )

    __table_args__ = (
        CheckConstraint(
            "(provider IS NULL AND source_key IS NULL) OR (provider IS NOT NULL AND source_key IS NOT NULL)",
            name="identity_pair",
        ),
        CheckConstraint(
            "provider IS NULL OR provider IN ('google_places', 'tavily', 'csv_import')",
            name="provider_domain",
        ),
        CheckConstraint(
            "provider IS DISTINCT FROM 'google_places' OR "
            "(place_id IS NOT NULL AND length(btrim(place_id)) > 0 AND "
            "source_key IS NOT NULL AND length(btrim(source_key)) > 0 AND place_id = source_key)",
            name="google_place_identity_matches",
        ),
        UniqueConstraint("workspace_id", "place_id", name="uq_business_workspace_place"),
        UniqueConstraint("workspace_id", "id", name="uq_businesses_workspace_id"),
        UniqueConstraint("workspace_id", "provider", "source_key", name="uq_business_workspace_provider_source"),
    )


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prospection_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    setter_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    closer_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    profile: Mapped[LeadProfile] = mapped_column(
        Enum(LeadProfile, values_callable=enum_values), default=LeadProfile.UNCLASSIFIED, nullable=False
    )
    validation_status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, values_callable=enum_values), default=ValidationStatus.PENDING, nullable=False
    )
    pipeline_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    score: Mapped[int | None] = mapped_column(nullable=True)
    score_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    giro: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    zona: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    web_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    web_check_detail: Mapped[str | None] = mapped_column(String(100), nullable=True)
    google_presence: Mapped[str | None] = mapped_column(String(50), nullable=True)
    meta_ads_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outreach_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_message_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outreach_message_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_import_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    priority: Mapped[int] = mapped_column(default=0, nullable=False)
    estimated_value: Mapped[float | None] = mapped_column(nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    first_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    website_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_presence_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_ads_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    business: Mapped["Business"] = relationship(
        back_populates="leads",
        primaryjoin="and_(Lead.business_id == Business.id, Lead.workspace_id == Business.workspace_id)",
        foreign_keys="[Lead.business_id, Lead.workspace_id]",
    )
    setter: Mapped["User | None"] = relationship(
        "User", foreign_keys=[setter_id], lazy="joined"
    )
    closer: Mapped["User | None"] = relationship(
        "User", foreign_keys=[closer_id], lazy="joined"
    )
    pipeline_stage: Mapped["PipelineStage | None"] = relationship(
        "PipelineStage", foreign_keys=[pipeline_stage_id], back_populates="leads", lazy="joined"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="lead", cascade="all, delete-orphan"
    )

    @property
    def setter_name(self) -> str | None:
        return self.setter.full_name if self.setter else None

    @property
    def closer_name(self) -> str | None:
        return self.closer.full_name if self.closer else None

    @property
    def pipeline_stage_name(self) -> str | None:
        return self.pipeline_stage.label if self.pipeline_stage else None

    __table_args__ = (
        UniqueConstraint("workspace_id", "business_id", name="uq_lead_workspace_business"),
        UniqueConstraint("workspace_id", "id", name="uq_leads_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "business_id"], ["businesses.workspace_id", "businesses.id"],
            name="fk_leads_business_tenant", ondelete="CASCADE",
        ),
        Index("ix_leads_workspace_setter", "workspace_id", "setter_id"),
        Index("ix_leads_workspace_closer", "workspace_id", "closer_id"),
        Index("ix_leads_validation_status", "validation_status"),
    )
