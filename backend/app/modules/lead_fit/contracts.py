"""Contratos puros de Lead Fit / Jev.

Modelos Pydantic sin dependencia de DB, settings ni proveedores.
extra="forbid" en todos los modelos para rechazar campos inesperados.
"""

import hashlib
import json
import math
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    field_validator,
    model_validator,
)


def canonical_sha256(value: dict) -> str:
    """SHA-256 canónico sobre JSON UTF-8 (spec §3.2)."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CriterionKind(str, PyEnum):
    REQUIRED = "required"
    PREFERENCE = "preference"
    EXCLUSION = "exclusion"


class CriterionEvaluator(str, PyEnum):
    SEMANTIC = "semantic"
    EXACT = "exact"


class AssessmentResult(str, PyEnum):
    MATCHES = "matches"
    DOES_NOT_MATCH = "does_not_match"
    NOT_ESTABLISHED = "not_established"
    CONFLICTING = "conflicting"


class FitClassification(str, PyEnum):
    QUALIFIED = "qualified"
    NEEDS_MORE_RESEARCH = "needs_more_research"
    NOT_QUALIFIED = "not_qualified"


class EvaluationStatus(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExactRuleFieldIn(BaseModel):
    """Regla exacta: el campo debe estar en una lista de valores permitidos."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["field_in"] = "field_in"
    field: str = Field(..., min_length=1, max_length=255)
    values: list[str] = Field(..., min_length=1, max_length=100)

    @field_validator("values", mode="before")
    @classmethod
    def _non_empty_values(cls, v):
        if any(not isinstance(item, str) or not item.strip() for item in v):
            raise ValueError("field_in values must be non-empty strings")
        return [item.strip() for item in v]


class ExactRuleNumericRange(BaseModel):
    """Regla exacta: un campo numérico dentro de límites con una unidad."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["numeric_range"] = "numeric_range"
    field: str = Field(..., min_length=1, max_length=255)
    min: float | None = Field(default=None, strict=True)
    max: float | None = Field(default=None, strict=True)
    unit: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _bounds_consistent(cls, v):
        if v.min is not None and v.max is not None and v.min > v.max:
            raise ValueError("numeric_range min cannot exceed max")
        return v


class ExactRuleGeoWithin(BaseModel):
    """Regla exacta: punto dentro de un centro/radio o polígono validado."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["geo_within"] = "geo_within"
    center_lat: float | None = Field(default=None, strict=True)
    center_lon: float | None = Field(default=None, strict=True)
    radius_km: float | None = Field(default=None, gt=0, strict=True)
    polygon: list[list[StrictFloat]] | None = None

    @model_validator(mode="after")
    def _geo_consistent(cls, v):
        has_center = v.center_lat is not None and v.center_lon is not None
        if (v.center_lat is None) != (v.center_lon is None):
            raise ValueError("geo_within center latitude and longitude must be paired")
        if has_center:
            if v.radius_km is None or v.polygon is not None:
                raise ValueError("geo_within center requires only a radius")
            if not -90 <= v.center_lat <= 90 or not -180 <= v.center_lon <= 180:
                raise ValueError("geo_within center coordinates are out of range")
        elif v.polygon is None:
            raise ValueError("geo_within requires a center/radius or polygon")
        if v.polygon is not None:
            if v.radius_km is not None or len(v.polygon) < 3:
                raise ValueError("geo_within polygon needs 3+ points and no radius")
            for point in v.polygon:
                if len(point) != 2 or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                    raise ValueError("geo_within polygon coordinates are out of range")
        return v


ExactRule = ExactRuleFieldIn | ExactRuleNumericRange | ExactRuleGeoWithin


class CriterionSpec(BaseModel):
    """Una condición de encaje: una sola proposición."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: CriterionKind
    description: str = Field(..., min_length=1, max_length=1000)
    source_pointer: str = Field(..., min_length=1, max_length=500)
    weight: int = Field(..., ge=0, le=100, strict=True)
    evaluator: CriterionEvaluator
    exact_rule: ExactRule | None = None
    must_resolve: bool = False

    @model_validator(mode="before")
    @classmethod
    def _default_must_resolve_required(cls, values):
        if isinstance(values, dict) and values.get("kind") == CriterionKind.REQUIRED:
            values = dict(values)
            values["must_resolve"] = True
        return values

    @model_validator(mode="after")
    def _weight_by_kind(cls, v):
        if v.kind is CriterionKind.EXCLUSION and v.weight != 0:
            raise ValueError("exclusion criteria must have weight 0")
        if v.kind in (CriterionKind.REQUIRED, CriterionKind.PREFERENCE) and v.weight < 1:
            raise ValueError("required/preference criteria need weight 1..100")
        if v.exact_rule is not None and v.evaluator is not CriterionEvaluator.EXACT:
            raise ValueError("exact_rule requires evaluator=exact")
        if v.evaluator is CriterionEvaluator.EXACT and v.exact_rule is None:
            raise ValueError("evaluator=exact requires exact_rule")
        return v


class FitPolicyContent(BaseModel):
    """Contenido de política de encaje (propuesta, no aprobación)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["fit-policy-1.0"] = "fit-policy-1.0"
    criteria: tuple[CriterionSpec, ...] = Field(..., min_length=1, max_length=10)
    accept_score: int = Field(70, ge=0, le=100, strict=True)
    min_coverage: int = Field(60, ge=0, le=100, strict=True)
    choice_confidence_floor: float = Field(0.80, ge=0.0, le=1.0, strict=True)
    entity_confidence_floor: float = Field(0.90, ge=0.0, le=1.0, strict=True)
    max_evidence_age_hours: int = Field(720, ge=1, le=8760, strict=True)

    @model_validator(mode="after")
    def _criterion_ids_unique(self):
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion IDs must be unique")
        return self

    @model_validator(mode="after")
    def _at_least_one_positive(cls, v):
        positive = [
            c for c in v.criteria if c.kind in (CriterionKind.REQUIRED, CriterionKind.PREFERENCE)
        ]
        if not positive:
            raise ValueError("al menos un criterio positivo es requerido")
        return v

    @property
    def total_weight(self) -> int:
        return sum(
            c.weight
            for c in self.criteria
            if c.kind in (CriterionKind.REQUIRED, CriterionKind.PREFERENCE)
        )


class FitPolicySnapshot(BaseModel):
    """Inmutable snapshot de política aprobada."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: UUID
    workspace_id: UUID
    playbook_id: UUID
    version: int
    commercial_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    content: FitPolicyContent
    policy_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    actor_id: UUID
    approved_at: datetime


class EvidenceItem(BaseModel):
    """Un ítem de evidencia atribución a una fuente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    workspace_id: UUID
    lead_id: UUID
    research_id: UUID | None = None
    provider: str = Field(..., min_length=1, max_length=50)
    type: str = Field(..., min_length=1, max_length=50)
    source_url: str = Field(..., min_length=1, max_length=2048)
    retrieved_at: datetime
    excerpt: str
    truncated: bool = False
    structured_facts: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    original_material_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    subject_status: str = Field(..., pattern=r"^(matched|mismatched|unresolved)$")
    association_method: str = Field(..., min_length=1, max_length=100)
    association_confidence: float | None = Field(default=None, ge=0.0, le=1.0, strict=True)


class ContactPoint(BaseModel):
    """Un punto de contacto de un lead."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: str = Field(..., pattern=r"^(email|whatsapp|phone|web_form)$")
    value: str = Field(..., min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    subject_status: str = Field(..., pattern=r"^(matched|mismatched|unresolved)$")
    valid_format: bool = False


class LeadDossier(BaseModel):
    """Expediente de un lead con identidad, hechos, contactos y fuentes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_id: UUID
    workspace_id: UUID
    name: str | None = None
    identity_keys: tuple[str, ...] = Field(default_factory=tuple)
    research_id: UUID | None = None
    sources: tuple[EvidenceItem, ...] = Field(default_factory=tuple, max_length=6)
    contacts: tuple[ContactPoint, ...] = Field(default_factory=tuple)
    source_snapshot_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    truncated: bool = False

    @model_validator(mode="after")
    def _limits(cls, v):
        if len(v.sources) > 6:
            raise ValueError("máximo 6 fuentes por evaluación")
        if len(v.contacts) > 10:
            raise ValueError("máximo 10 contactos por expediente")
        return v


class SourceAssessment(BaseModel):
    """Resultado de una pregunta cerrada de Jev para un criterio-fuente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str
    evidence_id: str
    status: AssessmentResult
    method: str = Field(..., pattern=r"^(code|jev)$")
    confidence: float = Field(..., ge=0.0, le=1.0, strict=True)
    probabilities: dict[str, float] = Field(default_factory=dict)

    @field_validator("probabilities", mode="before")
    @classmethod
    def _probabilities_keys(cls, v):
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("probabilities must be a dict")
        allowed = {"matches", "does_not_match", "not_established"}
        for key, val in v.items():
            if key not in allowed:
                raise ValueError(f"probability key {key!r} not allowed")
            if isinstance(val, bool) or not isinstance(val, int | float):
                raise ValueError(f"probabilidad de {key} debe ser número finito")
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                raise ValueError("probabilidad no puede ser NaN o infinito")
            if not 0.0 <= val <= 1.0:
                raise ValueError("probabilidades deben estar entre 0 y 1")
        return v

    @model_validator(mode="after")
    def _probabilities_valid(cls, v):
        if v.probabilities:
            total = sum(v.probabilities.values())
            if not math.isclose(total, 1.0, abs_tol=0.01):
                raise ValueError("las probabilidades deben sumar 1 ± 0.01")
            for key, val in v.probabilities.items():
                if not isinstance(val, int | float) or isinstance(val, bool):
                    raise ValueError(f"probabilidad de {key} debe ser número finito")
                if math.isnan(val) or math.isinf(val):
                    raise ValueError("probabilidad no puede ser NaN o infinito")
        return v


class CriterionAssessment(BaseModel):
    """Agregación de evaluaciones por criterio."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str
    result: AssessmentResult
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    method: str = Field(..., pattern=r"^(code|jev)$")
    confidence: float = Field(..., ge=0.0, le=1.0, strict=True)
    probabilities: dict[str, float] = Field(default_factory=dict)
    reason_code: str = Field(..., min_length=1, max_length=100)


class FitDecision(BaseModel):
    """Decisión de encaje del motor determinista."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classification: FitClassification
    fit_score: int | None = Field(default=None, ge=0, le=100)
    coverage: float = Field(..., ge=0.0, le=100.0)
    reasons: tuple[str, ...] = Field(default_factory=tuple)
    missing_criterion_ids: tuple[str, ...] = Field(default_factory=tuple)
    contact_ready: bool = False


class FitResearchPlan(BaseModel):
    """Plan de investigación incremental (T07)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_id: UUID
    evaluation_id: UUID
    policy_id: UUID
    source_digest: str
    plan_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    purpose: str = Field(..., pattern=r"^(fit|contact)$")
    missing_criterion_ids: tuple[str, ...] = Field(default_factory=tuple)
    query: str = Field(..., min_length=1, max_length=1000)
    source_strategy: str = Field(..., pattern=r"^(maps|web|mixed)$")
    max_search: int = Field(1, ge=0, le=1)
    max_extract: int = Field(1, ge=0, le=1)
    max_jev: int = Field(2, ge=0, le=2)
    max_rounds: int = Field(1, ge=1, le=1)
    monetary_cap_usd: float | None = Field(default=None, ge=0)
    expires_at: datetime | None = None


class FitEvaluationResult(BaseModel):
    """Resultado completo de una evaluación de encaje."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    contract_version: Literal["lead-fit-1.0"] = "lead-fit-1.0"
    status: EvaluationStatus
    classification: FitClassification | None = None
    fit_score: int | None = Field(default=None, ge=0, le=100)
    coverage: float | None = Field(default=None, ge=0.0, le=100.0)
    assessments: tuple[CriterionAssessment, ...] = Field(default_factory=tuple)
    reasons: tuple[str, ...] = Field(default_factory=tuple)
    missing_criterion_ids: tuple[str, ...] = Field(default_factory=tuple)
    contact_ready: bool = False
    contact_readiness: dict[str, bool] = Field(default_factory=dict)
    policy_digest: str
    source_digest: str
    question_digest: str
    engine_version: str = "lead-fit-1.0"
    eval_metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None

    @model_validator(mode="after")
    def _decision_fields_match_status(self):
        if self.status is EvaluationStatus.COMPLETED:
            if self.classification is None:
                raise ValueError("completed evaluations require a classification")
        elif any(
            value is not None for value in (self.classification, self.fit_score, self.coverage)
        ):
            raise ValueError("non-completed evaluations cannot include decision fields")
        return self


# ---------------------------------------------------------------------------
# Reconstrucción de modelos con referencias forward (ExactRule).
# ---------------------------------------------------------------------------

CriterionSpec.model_rebuild()
ExactRuleFieldIn.model_rebuild()
ExactRuleNumericRange.model_rebuild()
ExactRuleGeoWithin.model_rebuild()
FitPolicyContent.model_rebuild()


# ---------------------------------------------------------------------------
# Validación de integridad numérica (NaN/bools).
# ---------------------------------------------------------------------------


def _validate_no_nan_or_bool_numbers(value: Any) -> None:
    """Rechaza NaN, infinito y bools donde se esperan números."""
    if isinstance(value, bool):
        raise ValueError("los bools no son números válidos en este contexto")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError("números NaN o infinito no permitidos")
    if isinstance(value, dict):
        for item in value.values():
            _validate_no_nan_or_bool_numbers(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _validate_no_nan_or_bool_numbers(item)


def validate_policy_integrity(content: FitPolicyContent) -> None:
    """Valida consistencia de pesos, ausencia de NaN/bools numéricos y límites."""
    _validate_no_nan_or_bool_numbers(
        {"accept_score": content.accept_score, "min_coverage": content.min_coverage}
    )
    for criterion in content.criteria:
        if criterion.weight < 0:
            raise ValueError(f"peso negativo en criterio {criterion.id}")
        if not math.isfinite(criterion.weight):
            raise ValueError(f"peso no finito en criterio {criterion.id}")
        if criterion.exact_rule is not None:
            rule = criterion.exact_rule
            if isinstance(rule, ExactRuleFieldIn) and rule.field not in {
                "name", "category", "giro", "industry", "country", "city",
            }:
                raise ValueError(f"unsupported exact field {rule.field!r}")
            if isinstance(rule, ExactRuleNumericRange):
                if rule.field not in {"rating", "reviews_total"}:
                    raise ValueError(f"unsupported numeric exact field {rule.field!r}")
                _validate_no_nan_or_bool_numbers({"min": rule.min, "max": rule.max})
                if rule.field == "reviews_total" and any(
                    value is not None and (not float(value).is_integer() or value < 0)
                    for value in (rule.min, rule.max)
                ):
                    raise ValueError("reviews_total bounds must be nonnegative integers")
                if rule.field == "rating" and any(
                    value is not None and not 0 <= value <= 5 for value in (rule.min, rule.max)
                ):
                    raise ValueError("rating bounds must be between 0 and 5")
