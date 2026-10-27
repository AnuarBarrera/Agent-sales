from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


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
    location_label: str | None = Field(None, min_length=1, max_length=255)
    center_latitude: float | None = Field(None, ge=-90, le=90)
    center_longitude: float | None = Field(None, ge=-180, le=180)
    radius_km: float | None = Field(None, ge=0.1, le=50)
    polygon_geometry: dict | str | None = None  # GeoJSON Polygon or WKT
    discovery_strategy: Literal["maps"] | None = None
    search_query: str | None = Field(None, min_length=1, max_length=500)
    fit_policy_id: UUID | None = None
    candidate_limit: int = Field(10, ge=1, le=10)
    enrich_candidates: StrictBool = False

    @model_validator(mode="after")
    def validate_area(self) -> "ProspectionRunCreate":
        if self.discovery_strategy is None:
            if self.fit_policy_id is not None or self.search_query is not None:
                raise ValueError("fit policy and query require an explicit discovery strategy")
            if not self.location_label:
                raise ValueError("location_label is required for legacy Maps searches")
        else:
            if self.fit_policy_id is None or not self.search_query or not self.search_query.strip():
                raise ValueError("Lead Fit discovery requires an approved policy and reviewed query")
            if not self.location_label:
                raise ValueError("Maps discovery requires a selected location")

        if (self.center_latitude is None) != (self.center_longitude is None):
            raise ValueError("center_latitude y center_longitude deben enviarse juntos")
        if self.radius_km is None and self.polygon_geometry is None:
            raise ValueError("radius_km o polygon_geometry es requerido")
        if self.radius_km is not None and self.polygon_geometry is not None:
            raise ValueError("Selecciona un círculo o un polígono, no ambos")
        if self.enrich_candidates and (
            self.discovery_strategy != "maps" or self.candidate_limit > 3
        ):
            raise ValueError("Initial enrichment requires explicit Maps discovery capped at three candidates")
        return self


class ProspectionRunRead(BaseModel):
    id: UUID
    workspace_id: UUID
    created_by_id: UUID
    giro: str
    location_label: str
    center_latitude: float | None
    center_longitude: float | None
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
    fit_policy_id: UUID | None = None
    search_plan_snapshot: dict | None = None

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
    workspace_id: UUID
    run_id: UUID
    lead_id: UUID | None
    place_id: str | None
    provider: str | None
    source_key: str | None
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


class ProspectionRunFitResults(BaseModel):
    run_id: UUID
    found_count: int
    new_count: int
    duplicate_count: int
    discovery_failed_count: int
    evaluation_unavailable_count: int = 0
    total: int
    qualified: int
    needs_more_research: int
    not_qualified: int
    evaluation_failed: int
    pending: int
    items: list[dict]
