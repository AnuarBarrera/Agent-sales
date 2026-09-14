from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PipelineStageBase(BaseModel):
    key: str = Field(..., min_length=1, max_length=50)
    label: str = Field(..., min_length=1, max_length=100)
    order: int = Field(..., ge=0)
    is_active: bool = True


class PipelineStageCreate(PipelineStageBase):
    pass


class PipelineStageUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    order: int | None = Field(None, ge=0)
    is_active: bool | None = None


class PipelineStageRead(PipelineStageBase):
    id: UUID
    workspace_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PipelineStageReorder(BaseModel):
    stages: list[dict] = Field(..., min_length=1)
    # Each dict should have: {id: UUID, order: int, label?: str, key?: str}


class PipelineHistoryRead(BaseModel):
    id: UUID
    lead_id: UUID
    from_stage_id: UUID | None
    to_stage_id: UUID
    changed_by_id: UUID
    reason: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeadStageChangeRequest(BaseModel):
    stage_id: UUID
    reason: str | None = Field(None, max_length=500)
