from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ValidationCheckType(str, Enum):
    WEBSITE = "website"
    GOOGLE_PRESENCE = "google_presence"
    META_ADS = "meta_ads"


class ValidationResult(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    ACTIVE = "active"
    NONE = "none"
    NOT_FOUND = "not_found"
    PENDING = "pending"
    ERROR = "error"


class LeadValidationRead(BaseModel):
    id: UUID
    lead_id: UUID
    run_id: UUID | None
    check_type: ValidationCheckType
    result: ValidationResult
    detail: str | None
    provider: str
    provider_request_id: str | None
    checked_at: datetime
    duration_ms: int | None
    attempt: int

    model_config = ConfigDict(from_attributes=True)


class LeadProfile(str, Enum):
    UNCLASSIFIED = "unclassified"
    ORO = "oro"
    AGENCIA = "agencia"
    INQUILINO = "inquilino"
    FRIO = "frio"


class ValidationSnapshotRead(BaseModel):
    id: UUID
    lead_id: UUID
    website_result: ValidationResult | None
    google_presence_result: ValidationResult | None
    meta_ads_result: ValidationResult | None
    profile: LeadProfile | None
    score: int | None
    score_reason: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ValidationRunRequest(BaseModel):
    lead_ids: list[UUID] = Field(..., min_length=1)
    check_types: list[ValidationCheckType] = Field(
        default=[ValidationCheckType.WEBSITE, ValidationCheckType.GOOGLE_PRESENCE, ValidationCheckType.META_ADS]
    )
