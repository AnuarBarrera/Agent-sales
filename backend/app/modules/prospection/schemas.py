from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProspectionRunStatus(str, Enum):
    QUEUED = "queued"
    SEARCHING = "searching"
    ENRICHING = "enriching"
    DEDUPLICATING = "deduplicating"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


class RunLeadResult(str, Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    UPDATED = "updated"
    FAILED = "failed"


class ProspectionRunCreate(BaseModel):
    giro: str = Field(..., min_length=1, max_length=255)
    location_label: str = Field(..., min_length=1, max_length=255)
    center_latitude: float
    center_longitude: float
    radius_km: float | None = Field(None, ge=0.1, le=50)
    polygon_geometry: dict | str | None = None  # GeoJSON Polygon or WKT

    @model_validator(mode="after")
    def validate_area(self) -> "ProspectionRunCreate":
        if self.radius_km is None and self.polygon_geometry is None:
            raise ValueError("radius_km o polygon_geometry es requerido")
        if self.radius_km is not None and self.polygon_geometry is not None:
            raise ValueError("Selecciona un círculo o un polígono, no ambos")
        return self


class ProspectionRunRead(BaseModel):
    id: UUID
    workspace_id: UUID
    created_by_id: UUID
    giro: str
    location_label: str
    center_latitude: float
    center_longitude: float
    radius_km: float | None
    polygon_geometry: str | None
    status: ProspectionRunStatus
    provider: str
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    found_count: int
    new_count: int
    duplicate_count: int
    error_count: int
    error_summary: str | None

    model_config = ConfigDict(from_attributes=True)


class ProspectionRunListItem(BaseModel):
    id: UUID
    giro: str
    location_label: str
    status: ProspectionRunStatus
    found_count: int
    new_count: int
    duplicate_count: int
    error_count: int
    requested_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class RunLeadRead(BaseModel):
    id: UUID
    run_id: UUID
    lead_id: UUID | None
    place_id: str
    result: RunLeadResult
    raw_result: dict | None
    error_code: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RunEventRead(BaseModel):
    id: UUID
    run_id: UUID
    event_type: str
    payload: dict | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProspectionRunEventResponse(BaseModel):
    run_id: UUID
    status: ProspectionRunStatus
    progress: str | None = None
    found_count: int
    new_count: int
    duplicate_count: int
    error_count: int
    error_summary: str | None
    events: list[RunEventRead] = []
