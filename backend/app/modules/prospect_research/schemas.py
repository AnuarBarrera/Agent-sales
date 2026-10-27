from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.prospect_research.models import ProspectResearchStatus


class ProspectResearchRead(BaseModel):
    id: UUID
    workspace_id: UUID
    lead_id: UUID
    refresh_of_id: UUID | None
    status: ProspectResearchStatus
    provider: str
    operation: str
    query_summary: str
    query_context: dict[str, Any] | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    result_count: int
    extracted_count: int
    search_credits: int | None
    extract_credits: int | None
    total_credits: int | None
    provider_request_id: str | None
    created_at: datetime
    updated_at: datetime
    job_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ResearchEvidenceRead(BaseModel):
    id: UUID
    evidence_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
    workspace_id: UUID
    research_id: UUID
    lead_id: UUID
    provider: str
    operation: str
    source_type: str
    source_url: str
    source_title: str | None
    excerpt: str
    retrieved_at: datetime
    relevance_score: float | None
    metadata_json: dict[str, Any] | None

    model_config = ConfigDict(from_attributes=True)
