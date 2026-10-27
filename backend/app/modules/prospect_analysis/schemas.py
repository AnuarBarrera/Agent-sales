from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.ai.contracts import ProspectIntelligence, QualificationDecision
from app.modules.prospect_analysis.models import ProspectAnalysisStatus


class ProspectAnalysisRead(BaseModel):
    id: UUID
    workspace_id: UUID
    lead_id: UUID
    sales_playbook_id: UUID
    research_id: UUID
    fit_evaluation_id: UUID | None = None
    previous_analysis_id: UUID | None
    status: ProspectAnalysisStatus
    evidence_ids: list[str]
    intelligence: ProspectIntelligence | None
    qualification: QualificationDecision | None
    intelligence_provider: str | None
    intelligence_model: str | None
    intelligence_prompt_version: str | None
    intelligence_contract_version: str | None
    intelligence_latency_ms: int | None
    intelligence_tokens: int | None
    qualification_provider: str | None
    qualification_model: str | None
    qualification_prompt_version: str | None
    qualification_contract_version: str | None
    qualification_latency_ms: int | None
    qualification_tokens: int | None
    generated_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProspectIntelligenceRead(BaseModel):
    analysis_id: UUID
    sales_playbook_id: UUID
    research_id: UUID
    provider: str | None
    model: str | None
    prompt_version: str | None
    contract_version: str | None
    generated_at: datetime | None
    intelligence: ProspectIntelligence


class QualificationRead(BaseModel):
    analysis_id: UUID
    sales_playbook_id: UUID
    research_id: UUID
    provider: str | None
    model: str | None
    prompt_version: str | None
    contract_version: str | None
    generated_at: datetime | None
    qualification: QualificationDecision
