"""Schemas Pydantic de Lead Fit / Jev (T03)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.lead_fit.contracts import (
    CriterionSpec,
    FitPolicyContent,
    FitPolicySnapshot,
    FitResearchPlan,
)


class FitPolicyPreview(BaseModel):
    """Respuesta de preview de política (cero proveedores, no autoridad)."""

    model_config = ConfigDict(from_attributes=True)

    content: FitPolicyContent
    expected_policy_digest: str
    expected_commercial_digest: str


class FitPolicyPreviewRequest(BaseModel):
    """Identify the approved playbook whose authority seeds a proposal."""

    model_config = ConfigDict(extra="forbid")

    playbook_id: UUID


class FitPolicyCreate(BaseModel):
    """Payload de aprobación de política (ADMIN)."""

    model_config = ConfigDict(extra="forbid")

    playbook_id: UUID
    expected_commercial_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    content: FitPolicyContent
    confirmed: bool = False

class FitPolicyRead(BaseModel):
    """Lectura de política aprobada."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    playbook_id: UUID
    version: int
    commercial_digest: str
    content: FitPolicyContent
    policy_digest: str
    actor_id: UUID
    approved_at: datetime
    status: str


class FitEvaluationCreate(BaseModel):
    """Payload para crear/reutilizar una evaluación de encaje."""

    model_config = ConfigDict(extra="forbid")

    expected_policy_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    expected_source_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    retry_of: UUID | None = None
    reenqueue: bool = False


class FitEvaluationRead(BaseModel):
    """Lectura de evaluación de encaje (técnica + resultado acotado)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    lead_id: UUID
    policy_id: UUID
    research_id: UUID | None
    previous_id: UUID | None
    policy_digest: str
    source_digest: str
    question_digest: str | None
    engine_version: str
    requested_model: str
    response_model: str | None
    result_digest: str | None
    status: str
    classification: str | None
    fit_score: int | None
    coverage: float | None
    result: dict[str, Any] | None
    audit: dict[str, Any] | None
    call_count: int
    error_code: str | None
    requested_at: datetime
    started_at: datetime | None
    dispatched_at: datetime | None
    completed_at: datetime | None


class FitEvaluationContext(BaseModel):
    """Contexto server-owned para preview de evaluación (cero proveedores)."""

    policy_id: UUID | None
    policy_digest: str | None
    expected_source_digest: str
    research_id: UUID | None = None
    # Snapshot aditivo y opcional de la MISMA política ya verificada que produjo
    # policy_id/policy_digest. No es una autoridad nueva ni una segunda lectura.
    # Su ausencia significa "detalle no disponible", nunca un estado reconstruido.
    policy: FitPolicyRead | None = None


class FitResearchPlanSchema(BaseModel):
    """Plan de investigación incremental."""

    model_config = ConfigDict(from_attributes=True)

    lead_id: UUID
    evaluation_id: UUID
    policy_id: UUID
    source_digest: str
    plan_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    purpose: str
    missing_criterion_ids: list[str]
    query: str
    source_strategy: str
    max_search: int
    max_extract: int
    max_jev: int
    max_rounds: int
    monetary_cap_usd: float | None
    expires_at: datetime | None


class FitResearchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field("fit", pattern=r"^(fit|contact)$")


class FitResearchAuthorize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(..., pattern=r"^(fit|contact)$")
    expected_plan_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = False
    monetary_cap_usd: float | None = Field(default=None, ge=0)


class FitResearchGrantRead(BaseModel):
    """Lectura de grant de investigación."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    lead_id: UUID
    evaluation_id: UUID
    policy_id: UUID
    source_digest: str
    plan_hash: str
    status: str
    consumed_search: int
    consumed_extract: int
    consumed_jev: int
    consumed_usd: float | None
    reserved_search: int
    reserved_extract: int
    reserved_jev: int
    error_code: str | None
    result_evaluation_id: UUID | None
    unchanged_rounds: int
    expires_at: datetime | None
    max_search: int
    max_extract: int
    max_jev: int
    max_rounds: int
    monetary_cap_usd: float | None


class FitDecisionSchema(BaseModel):
    """Decisión de encaje serializada."""

    model_config = ConfigDict(from_attributes=True)

    classification: str
    fit_score: int | None
    coverage: float
    reasons: tuple[str, ...]
    missing_criterion_ids: tuple[str, ...]
    contact_ready: bool


# Reconstruir modelos importados de contracts.py para resolver forward refs de
# Pydantic (UUID, etc.) en este módulo, evitando PydanticUndefinedAnnotation.
from app.modules.lead_fit.contracts import (  # noqa: E402
    FitPolicyContent,
)

CriterionSpec.model_rebuild()
FitPolicyContent.model_rebuild()
FitPolicySnapshot.model_rebuild()
FitResearchPlan.model_rebuild()
