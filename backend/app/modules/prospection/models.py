import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
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
    center_latitude: Mapped[float] = mapped_column(nullable=False)
    center_longitude: Mapped[float] = mapped_column(nullable=False)
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

    run_leads: Mapped[list["RunLead"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    run_events: Mapped[list["RunEvent"]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_prospection_runs_workspace_status", "workspace_id", "status"),
        Index("ix_prospection_runs_created_by", "created_by_id"),
    )


class RunLead(Base):
    __tablename__ = "run_leads"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prospection_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[RunLeadResult] = mapped_column(Enum(RunLeadResult, values_callable=enum_values), nullable=False)
    raw_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    run: Mapped["ProspectionRun"] = relationship(back_populates="run_leads")
    lead: Mapped[Optional["Lead"]] = relationship()

    __table_args__ = (
        UniqueConstraint("run_id", "place_id", name="uq_run_lead_run_place"),
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
