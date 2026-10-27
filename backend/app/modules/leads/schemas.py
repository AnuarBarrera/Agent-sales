from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# Business schemas
class BusinessBase(BaseModel):
    place_id: str | None = Field(None, max_length=255)
    provider: str | None = Field(None, max_length=50)
    source_key: str | None = Field(None, max_length=512)
    name: str = Field(..., min_length=1, max_length=255)
    address: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    website: str | None = Field(None, max_length=500)
    rating: float | None = None
    reviews_total: int | None = None
    latitude: float | None = None
    longitude: float | None = None


class BusinessCreate(BusinessBase):
    pass


class BusinessRead(BusinessBase):
    id: UUID
    workspace_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeadProfile(str, Enum):
    UNCLASSIFIED = "unclassified"
    ORO = "oro"
    AGENCIA = "agencia"
    INQUILINO = "inquilino"
    FRIO = "frio"


class ValidationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    ERROR = "error"


class LeadBase(BaseModel):
    profile: LeadProfile = LeadProfile.UNCLASSIFIED
    validation_status: ValidationStatus = ValidationStatus.PENDING
    priority: int = 0
    estimated_value: float | None = None
    next_follow_up_at: datetime | None = None


class LeadCreate(LeadBase):
    workspace_id: UUID
    business_id: UUID
    source_run_id: UUID | None = None
    setter_id: UUID | None = None
    closer_id: UUID | None = None


class LeadUpdate(BaseModel):
    setter_id: UUID | None = None
    closer_id: UUID | None = None
    profile: LeadProfile | None = None
    validation_status: ValidationStatus | None = None
    pipeline_stage_id: UUID | None = None
    score: int | None = None
    score_reason: str | None = Field(None, max_length=500)
    priority: int | None = None
    estimated_value: float | None = None
    lost_reason: str | None = Field(None, max_length=500)
    next_follow_up_at: datetime | None = None


class LeadRead(LeadBase):
    id: UUID
    workspace_id: UUID
    business_id: UUID
    source_run_id: UUID | None
    setter_id: UUID | None
    closer_id: UUID | None
    pipeline_stage_id: UUID | None
    score: int | None
    score_reason: str | None
    giro: str | None
    zona: str | None
    web_status: str | None
    web_check_detail: str | None
    google_presence: str | None
    meta_ads_active: bool | None
    contacted: bool
    source_searched_at: datetime | None
    outreach_message: str | None
    outreach_message_generated_at: datetime | None
    outreach_message_error: str | None
    last_activity_at: datetime | None
    first_contacted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    # Joined fields
    business: Optional["BusinessRead"] = None
    setter_name: str | None = None
    closer_name: str | None = None
    pipeline_stage_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class LeadListItem(BaseModel):
    id: UUID
    name: str  # from business
    address: str | None
    phone: str | None
    website: str | None
    profile: LeadProfile
    validation_status: ValidationStatus
    pipeline_stage_name: str | None
    setter_name: str | None
    closer_name: str | None
    priority: int
    next_follow_up_at: datetime | None
    last_activity_at: datetime | None
    contacted: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeadFilters(BaseModel):
    q: str | None = None
    profile: LeadProfile | None = None
    validation_status: ValidationStatus | None = None
    setter_id: UUID | None = None
    closer_id: UUID | None = None
    run_id: UUID | None = None
    pipeline_stage: UUID | None = None
    has_website: bool | None = None
    cursor: str | None = None
    limit: int = Field(default=50, le=100)


class LeadListResponse(BaseModel):
    items: list[LeadListItem]
    next_cursor: str | None = None


class LeadImportResponse(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str]


# Bulk assignment
class BulkAssignRequest(BaseModel):
    lead_ids: list[UUID] = Field(..., min_length=1)
    setter_id: UUID | None = None
    closer_id: UUID | None = None


class BulkAssignResponse(BaseModel):
    assigned: list[UUID]
    failed: dict[UUID, str]


# Forward reference resolution
LeadRead.model_rebuild()
