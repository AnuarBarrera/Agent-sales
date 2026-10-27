import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
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
    from app.modules.leads.models import Lead


class ProspectionRunStatus(str, PyEnum):
    QUEUED = "queued"
    SEARCHING = "searching"
    ENRICHING = "enriching"
    DEDUPLICATING = "deduplicating"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


class RunLeadResult(str, PyEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    UPDATED = "updated"
    FAILED = "failed"


class ProspectionRun(Base):
    __tablename__ = "prospection_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True
    )
    giro: Mapped[str] = mapped_column(String(255), nullable=False)
    location_label: Mapped[str] = mapped_column(String(255), nullable=False)
    center_latitude: Mapped[float | None] = mapped_column(nullable=True)
    center_longitude: Mapped[float | None] = mapped_column(nullable=True)
    radius_km: Mapped[float | None] = mapped_column(nullable=True)
    polygon_geometry: Mapped[str | None] = mapped_column(Geometry(geometry_type="POLYGON", srid=4326, from_text="ST_GeomFromEWKT", name="geometry"), nullable=True)
    status: Mapped[ProspectionRunStatus] = mapped_column(
        Enum(ProspectionRunStatus, values_callable=enum_values), default=ProspectionRunStatus.QUEUED, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), default="google_places", nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    found_count: Mapped[int] = mapped_column(default=0, nullable=False)
    new_count: Mapped[int] = mapped_column(default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_policy_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    search_plan_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run_leads: Mapped[list["RunLead"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        primaryjoin="and_(ProspectionRun.id == RunLead.run_id, ProspectionRun.workspace_id == RunLead.workspace_id)",
        foreign_keys="[RunLead.run_id]",
    )
    run_events: Mapped[list["RunEvent"]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "(center_latitude IS NULL AND center_longitude IS NULL) OR "
            "(center_latitude BETWEEN -90 AND 90 AND center_longitude BETWEEN -180 AND 180)",
            name="center_coordinates_pair",
        ),
        Index("ix_prospection_runs_workspace_status", "workspace_id", "status"),
        Index("ix_prospection_runs_created_by", "created_by_id"),
        UniqueConstraint("workspace_id", "id", name="uq_prospection_runs_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "fit_policy_id"], ["lead_fit_policies.workspace_id", "lead_fit_policies.id"],
            name="fk_prospection_runs_fit_policy_tenant", ondelete="RESTRICT",
        ),
    )


class RunLead(Base):
    __tablename__ = "run_leads"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result: Mapped[RunLeadResult] = mapped_column(Enum(RunLeadResult, values_callable=enum_values), nullable=False)
    raw_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    run: Mapped["ProspectionRun"] = relationship(
        back_populates="run_leads",
        primaryjoin="and_(RunLead.run_id == ProspectionRun.id, RunLead.workspace_id == ProspectionRun.workspace_id)",
        foreign_keys="[RunLead.run_id]",
    )
    lead: Mapped[Optional["Lead"]] = relationship(
        primaryjoin="and_(RunLead.lead_id == Lead.id, RunLead.workspace_id == Lead.workspace_id)",
        foreign_keys="[RunLead.lead_id]",
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
        UniqueConstraint("run_id", "place_id", name="uq_run_lead_run_place"),
        UniqueConstraint("workspace_id", "id", name="uq_run_leads_workspace_id"),
        UniqueConstraint("run_id", "provider", "source_key", name="uq_run_lead_run_provider_source"),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["prospection_runs.workspace_id", "prospection_runs.id"],
            name="fk_run_leads_run_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "lead_id"], ["leads.workspace_id", "leads.id"],
            name="fk_run_leads_lead_tenant", ondelete="RESTRICT",
        ),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prospection_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    run: Mapped["ProspectionRun"] = relationship(back_populates="run_events")

    __table_args__ = (
        Index("ix_run_events_run_created", "run_id", "created_at"),
    )
