"""Source-faithful normative assertions and finite, fail-closed policy compilation.

Extraction and evidence review happen upstream. This module never derives a
semantic assertion, concept, scope, condition, or maturity from descriptive text.
"""

import unicodedata
from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.modules.sales_playbook.schemas import (
    PLAYBOOK_PROVENANCE_SOURCE_REF_MAX_LENGTH,
    PlaybookMaturity,
    PlaybookOrigin,
    PlaybookProvenance,
    PlaybookRuleCode,
    PolicyRuleEffect,
    WorkflowStageRule,
)

MAX_SOURCE_SECTION_ID_LENGTH = 512


class SemanticModality(str, Enum):
    REQUIRED = "required"
    PROHIBITED = "prohibited"
    RECOMMENDED = "recommended"
    PERMITTED = "permitted"


class SemanticConcept(str, Enum):
    """Optional atomic normalization, independent of effect and workflow stage."""

    SERVICE_ALERT = "service_alert"
    COMMERCIAL_PITCH = "commercial_pitch"
    PRODUCT_REVEAL = "product_reveal"
    PRICE_MENTION = "price_mention"
    LOW_FRICTION_RESPONSE = "low_friction_response"
    TECHNICAL_JARGON = "technical_jargon"
    UNSUPPORTED_PRAISE = "unsupported_praise"
    RECIPIENT_LANGUAGE_ADAPTATION = "recipient_language_adaptation"


def _bounded_unique_ids(values: list[str]) -> list[str]:
    if any(not value.strip() or len(value) > MAX_SOURCE_SECTION_ID_LENGTH for value in values):
        raise ValueError("source identifiers must be nonblank and bounded")
    if len(values) != len(set(values)):
        raise ValueError("source identifiers must be unique")
    return values


class SemanticPolicyAssertion(BaseModel):
    """One source-backed normative claim; normalization and maturity may be unresolved."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: str = Field(min_length=1, max_length=200)
    assertion_text: str = Field(min_length=1, max_length=3000)
    modality: SemanticModality
    concept: SemanticConcept | None = None
    scope_text: str | None = Field(default=None, min_length=1, max_length=500)
    condition_text: str | None = Field(default=None, min_length=1, max_length=500)
    maturity: PlaybookMaturity | None = None
    maturity_origin: str | None = Field(default=None, pattern="^(source_assertion|governance)$")
    maturity_source_section_ids: list[str] = Field(default_factory=list, max_length=27)
    source_section_ids: list[str] = Field(min_length=1, max_length=27)

    @field_validator("assertion_id", "assertion_text", "scope_text", "condition_text")
    @classmethod
    def _text_is_present(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value

    @field_validator("source_section_ids", "maturity_source_section_ids")
    @classmethod
    def _validate_ids(cls, values: list[str]) -> list[str]:
        return _bounded_unique_ids(values)

    @model_validator(mode="after")
    def _validate_maturity_provenance(self):
        if self.maturity is None:
            if self.maturity_origin is not None or self.maturity_source_section_ids:
                raise ValueError("unresolved maturity cannot have authority metadata")
        elif self.maturity_origin == "governance":
            if not self.maturity_source_section_ids:
                raise ValueError("governance maturity needs separate governance provenance")
        elif self.maturity_source_section_ids:
            raise ValueError("direct maturity cannot claim governance provenance")
        elif self.maturity_origin is None:
            self.maturity_origin = "source_assertion"
        return self


class MaturityEvidence(BaseModel):
    """Explicit governance authority applying to named assertions only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1, max_length=200)
    source_text: str = Field(min_length=1, max_length=1000)
    maturity: PlaybookMaturity
    applies_to_assertion_ids: list[str] = Field(min_length=1, max_length=20)
    source_section_ids: list[str] = Field(min_length=1, max_length=27)

    @field_validator("evidence_id", "source_text")
    @classmethod
    def _text_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("governance evidence must not be blank")
        return value

    @field_validator("applies_to_assertion_ids", "source_section_ids")
    @classmethod
    def _validate_ids(cls, values: list[str]) -> list[str]:
        return _bounded_unique_ids(values)


def resolve_semantic_maturity(
    assertions: Iterable[SemanticPolicyAssertion],
    evidence: Iterable[MaturityEvidence],
) -> list[SemanticPolicyAssertion]:
    """Apply only explicit ID-scoped authority; direct maturity wins."""

    source = list(assertions)
    ids = [item.assertion_id for item in source]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate semantic assertion IDs")
    by_id: dict[str, list[MaturityEvidence]] = {}
    for authority in evidence:
        for assertion_id in authority.applies_to_assertion_ids:
            if assertion_id not in ids:
                raise ValueError(f"governance evidence references unknown assertion {assertion_id}")
            by_id.setdefault(assertion_id, []).append(authority)

    resolved = []
    for item in source:
        authorities = by_id.get(item.assertion_id, [])
        if len({authority.maturity for authority in authorities}) > 1:
            raise ValueError(f"conflicting maturity authorities for {item.assertion_id}")
        if item.maturity is not None or not authorities:
            resolved.append(item)
            continue
        governance_ids = _ordered_ids(
            section for authority in authorities for section in authority.source_section_ids
        )
        resolved.append(SemanticPolicyAssertion.model_validate(item.model_dump() | {
            "maturity": authorities[0].maturity,
            "maturity_origin": "governance",
            "maturity_source_section_ids": governance_ids,
        }))
    return resolved


class CompilationStatus(str, Enum):
    COMPILED = "compiled"
    UNKNOWN_CONCEPT = "unknown_concept"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"
    UNSUPPORTED = "unsupported"


class CompiledPolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: WorkflowStageRule
    source_assertion_ids: list[str] = Field(min_length=1)
    source_section_ids: list[str] = Field(min_length=1)
    maturity_source_section_ids: list[str] = Field(default_factory=list)


class UnresolvedSemanticPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertions: list[SemanticPolicyAssertion] = Field(min_length=1)
    status: CompilationStatus
    reason: str = Field(min_length=1)
    source_section_ids: list[str] = Field(min_length=1)

    @computed_field
    @property
    def assertion_ids(self) -> list[str]:
        return [item.assertion_id for item in self.assertions]


class SemanticCompilation(BaseModel):
    """Every input contributes to one trace or remains available for review."""

    model_config = ConfigDict(extra="forbid")

    input_assertion_ids: list[str] = Field(default_factory=list)
    compiled_rules: list[CompiledPolicyRule] = Field(default_factory=list)
    unresolved: list[UnresolvedSemanticPolicy] = Field(default_factory=list)


_ONE_TO_ONE: dict[tuple[SemanticConcept, SemanticModality], tuple[PlaybookRuleCode, PolicyRuleEffect]] = {
    (SemanticConcept.PRICE_MENTION, SemanticModality.PROHIBITED):
        (PlaybookRuleCode.NO_PREMATURE_PRICING, PolicyRuleEffect.MUST_NOT),
    (SemanticConcept.PRODUCT_REVEAL, SemanticModality.PROHIBITED):
        (PlaybookRuleCode.NO_PREMATURE_PRODUCT_REVEAL, PolicyRuleEffect.MUST_NOT),
    (SemanticConcept.LOW_FRICTION_RESPONSE, SemanticModality.REQUIRED):
        (PlaybookRuleCode.MINIMUM_FRICTION_CTA, PolicyRuleEffect.MUST),
    (SemanticConcept.TECHNICAL_JARGON, SemanticModality.PROHIBITED):
        (PlaybookRuleCode.NO_TECHNICAL_JARGON, PolicyRuleEffect.MUST_NOT),
    (SemanticConcept.UNSUPPORTED_PRAISE, SemanticModality.PROHIBITED):
        (PlaybookRuleCode.NO_FALSE_FLATTERY, PolicyRuleEffect.MUST_NOT),
    (SemanticConcept.RECIPIENT_LANGUAGE_ADAPTATION, SemanticModality.REQUIRED):
        (PlaybookRuleCode.ENTITY_LANGUAGE_ADAPTATION, PolicyRuleEffect.MUST),
}

_BOUNDED_COMMERCIAL_SCOPES: dict[SemanticConcept, frozenset[str]] = {
    SemanticConcept.PRICE_MENTION: frozenset({
        "touch 1", "toque 1", "touch 1 before the authorized commercial point",
        "toque 1 antes del punto comercial autorizado",
    }),
    SemanticConcept.PRODUCT_REVEAL: frozenset({
        "touch 1", "toque 1", "touch 1 before product disclosure",
        "toque 1, antes de revelar el producto",
    }),
}


def _normalized(value: str | None) -> str | None:
    return unicodedata.normalize("NFC", " ".join(value.split()).casefold()) if value else None


def _ordered_ids(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _source_ids(items: list[SemanticPolicyAssertion]) -> list[str]:
    return _ordered_ids(section for item in items for section in item.source_section_ids)


def _unresolved(
    items: list[SemanticPolicyAssertion], status: CompilationStatus, reason: str,
) -> UnresolvedSemanticPolicy:
    return UnresolvedSemanticPolicy(
        assertions=items, status=status, reason=reason, source_section_ids=_source_ids(items),
    )


def _guard(items: list[SemanticPolicyAssertion], *, bounded: SemanticConcept | None = None):
    scopes = {_normalized(item.scope_text) for item in items}
    maturities = {item.maturity for item in items}
    conditions = {_normalized(item.condition_text) for item in items}
    if len(scopes) != 1 or None in scopes:
        return CompilationStatus.AMBIGUOUS, "scope is absent or inconsistent"
    if len(maturities) != 1:
        return CompilationStatus.AMBIGUOUS, "maturity authorities disagree"
    if None in maturities:
        return CompilationStatus.UNSUPPORTED, "maturity has no explicit authority"
    if len(conditions) != 1 or None not in conditions:
        return CompilationStatus.AMBIGUOUS, "free-text conditions cannot become executable conditions"
    if bounded and next(iter(scopes)) not in _BOUNDED_COMMERCIAL_SCOPES[bounded]:
        return CompilationStatus.AMBIGUOUS, "scope is outside bounded commercial phase"
    if len("\n".join(item.assertion_text for item in items)) > 3000:
        return CompilationStatus.UNSUPPORTED, "source assertions exceed executable description capacity"
    if len(",".join(_source_ids(items))) > PLAYBOOK_PROVENANCE_SOURCE_REF_MAX_LENGTH:
        return CompilationStatus.UNSUPPORTED, "source provenance exceeds executable reference capacity"
    return None


def _trace(
    items: list[SemanticPolicyAssertion], code: PlaybookRuleCode, effect: PolicyRuleEffect,
) -> CompiledPolicyRule:
    source_ids = _source_ids(items)
    governance_ids = _ordered_ids(
        section for item in items for section in item.maturity_source_section_ids
    )
    first = items[0]
    assert first.scope_text is not None and first.maturity is not None
    rule = WorkflowStageRule(
        code=code, title=code.name.replace("_", " ").title(),
        description="\n".join(item.assertion_text for item in items),
        applies_to=[first.scope_text], effect=effect, condition=None,
        maturity=first.maturity,
        provenance=PlaybookProvenance(
            origin=PlaybookOrigin.MANUAL, source_ref=",".join(source_ids),
        ),
    )
    return CompiledPolicyRule(
        rule=rule, source_assertion_ids=[item.assertion_id for item in items],
        source_section_ids=source_ids, maturity_source_section_ids=governance_ids,
    )


def compile_semantic_policies(
    assertions: Iterable[SemanticPolicyAssertion],
) -> SemanticCompilation:
    """Compile finite, explicit 1:1 and N:1 mappings without filling missing facts."""

    source = list(assertions)
    ids = [item.assertion_id for item in source]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate semantic assertion IDs")
    result = SemanticCompilation(input_assertion_ids=ids)
    conflicts: dict[tuple[SemanticConcept, str, str | None], list[SemanticPolicyAssertion]] = {}
    for item in source:
        scope = _normalized(item.scope_text)
        if item.concept is not None and scope is not None:
            conflicts.setdefault((item.concept, scope, _normalized(item.condition_text)), []).append(item)
    conflicted_ids: set[str] = set()
    for group in conflicts.values():
        modalities = {item.modality for item in group}
        if SemanticModality.PROHIBITED in modalities and len(modalities) > 1:
            result.unresolved.append(_unresolved(
                group, CompilationStatus.CONFLICT, "opposite modalities share scope and condition",
            ))
            conflicted_ids.update(item.assertion_id for item in group)

    maturity_groups: dict[
        tuple[SemanticConcept, SemanticModality, str | None, str | None],
        list[SemanticPolicyAssertion],
    ] = {}
    for item in source:
        if item.concept is not None and item.assertion_id not in conflicted_ids:
            maturity_groups.setdefault((
                item.concept, item.modality, _normalized(item.scope_text),
                _normalized(item.condition_text),
            ), []).append(item)
    for group in maturity_groups.values():
        if len({item.maturity for item in group}) > 1:
            result.unresolved.append(_unresolved(
                group, CompilationStatus.AMBIGUOUS, "maturity authorities disagree",
            ))
            conflicted_ids.update(item.assertion_id for item in group)

    groups: dict[tuple[SemanticConcept, SemanticModality, str | None, str | None, PlaybookMaturity | None], list[SemanticPolicyAssertion]] = {}
    for item in source:
        if item.assertion_id in conflicted_ids:
            continue
        if item.concept is None:
            result.unresolved.append(_unresolved(
                [item], CompilationStatus.UNKNOWN_CONCEPT, "normalization is unresolved",
            ))
            continue
        identity = (
            item.concept, item.modality, _normalized(item.scope_text),
            _normalized(item.condition_text), item.maturity,
        )
        groups.setdefault(identity, []).append(item)

    service_groups: dict[str | None, list[SemanticPolicyAssertion]] = {}
    pitch_groups: dict[str | None, list[SemanticPolicyAssertion]] = {}
    for group in groups.values():
        first = group[0]
        if first.concept is SemanticConcept.SERVICE_ALERT and first.modality is SemanticModality.REQUIRED:
            service_groups.setdefault(_normalized(first.scope_text), []).extend(group)
        elif first.concept is SemanticConcept.COMMERCIAL_PITCH and first.modality is SemanticModality.PROHIBITED:
            pitch_groups.setdefault(_normalized(first.scope_text), []).extend(group)
        elif first.concept in {SemanticConcept.SERVICE_ALERT, SemanticConcept.COMMERCIAL_PITCH}:
            result.unresolved.append(_unresolved(group, CompilationStatus.UNSUPPORTED, "composite prerequisites absent"))
        else:
            mapping = _ONE_TO_ONE.get((first.concept, first.modality))
            if mapping is None:
                result.unresolved.append(_unresolved(group, CompilationStatus.UNSUPPORTED, "no allowlisted mapping"))
                continue
            guard = _guard(group, bounded=first.concept if first.concept in _BOUNDED_COMMERCIAL_SCOPES else None)
            if guard:
                result.unresolved.append(_unresolved(group, *guard))
            else:
                result.compiled_rules.append(_trace(group, *mapping))

    positions = {item.assertion_id: index for index, item in enumerate(source)}
    for scope in _ordered_ids([*service_groups, *pitch_groups]):
        alert = service_groups.get(scope, [])
        pitch = pitch_groups.get(scope, [])
        pair = sorted([*alert, *pitch], key=lambda item: positions[item.assertion_id])
        if not alert or not pitch:
            result.unresolved.append(_unresolved(pair, CompilationStatus.UNSUPPORTED, "composite prerequisites absent"))
            continue
        guard = _guard(pair)
        if guard:
            result.unresolved.append(_unresolved(pair, *guard))
        else:
            result.compiled_rules.append(_trace(
                pair, PlaybookRuleCode.SERVICE_ALERT_NOT_PITCH, PolicyRuleEffect.MUST,
            ))

    result.compiled_rules.sort(key=lambda trace: min(positions[item] for item in trace.source_assertion_ids))
    result.unresolved.sort(key=lambda item: min(positions[assertion_id] for assertion_id in item.assertion_ids))
    return result
