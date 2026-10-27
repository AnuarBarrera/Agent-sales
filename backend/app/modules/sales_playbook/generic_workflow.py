"""Owner-neutral workflow extraction over deterministic generic source units."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from app.ai.validation import contract_response_format
from app.integrations.llm import LLMGeneration
from app.modules.sales_playbook.extraction import (
    CompactWorkflowExtraction,
    CompactWorkflowStage,
    CompactWorkflowTransition,
    ExtractionAssemblyError,
    WorkflowConflictCandidate,
    WorkflowStageConflict,
)
from app.modules.sales_playbook.generic_identity import stage_id as _application_stage_id
from app.modules.sales_playbook.generic_provenance import (
    build_local_source_reference_map,
    resolve_local_source_references,
)
from app.modules.sales_playbook.schemas import (
    PlaybookMaturity,
    WorkflowConditionCode,
    WorkflowOutcome,
    WorkflowStageCode,
)
from app.modules.sales_playbook.source_planning import GenericExtractionUnit
from app.modules.sales_playbook.stage_aware import WorkflowUnitStage, WorkflowUnitTransition


class _GenericWorkflowStageFields(BaseModel):
    """Source workflow stage with an optional, never-required canonical mapping."""

    model_config = ConfigDict(extra="forbid")

    source_name: str = Field(min_length=1, max_length=300)
    canonical_stage_code: WorkflowStageCode | None = None
    objective: str | None = Field(default=None, max_length=2000)
    actions: list[str] = Field(default_factory=list, max_length=20)
    entry_conditions: list[WorkflowConditionCode] = Field(default_factory=list, max_length=10)
    exit_conditions: list[WorkflowConditionCode] = Field(default_factory=list, max_length=10)
    entry_condition_text: list[str] = Field(default_factory=list, max_length=10)
    exit_condition_text: list[str] = Field(default_factory=list, max_length=10)
    timing: str | None = Field(default=None, max_length=500)
    responsible_role: str | None = Field(default=None, max_length=100)
    maturity: PlaybookMaturity | None = None


class GenericWorkflowStageClaim(_GenericWorkflowStageFields):
    """Application-side stage claim with resolved persistent provenance."""

    source_stage_id: str = Field(min_length=1, max_length=120)
    source_section_ids: list[str] = Field(min_length=1, max_length=27)


class _ProviderGenericWorkflowStageClaim(_GenericWorkflowStageFields):
    source_refs: list[str] = Field(min_length=1, max_length=27)


class _GenericWorkflowTransitionFields(BaseModel):
    """Source workflow relation with optional canonical stages/condition/outcome."""

    model_config = ConfigDict(extra="forbid")

    from_stage_name: str = Field(min_length=1, max_length=300)
    from_stage_code: WorkflowStageCode | None = None
    to_stage_name: str = Field(min_length=1, max_length=300)
    to_stage_code: WorkflowStageCode | None = None
    condition_text: str = Field(min_length=1, max_length=1000)
    condition_code: WorkflowConditionCode | None = None
    outcome_text: str | None = Field(default=None, max_length=1000)
    outcome: WorkflowOutcome | None = None
    maturity: PlaybookMaturity | None = None
    wait_hours: int | None = Field(default=None, ge=0, le=8760)


class GenericWorkflowTransitionClaim(_GenericWorkflowTransitionFields):
    """Application-side transition claim with resolved source provenance."""

    source_section_ids: list[str] = Field(min_length=1, max_length=27)


class _ProviderGenericWorkflowTransitionClaim(_GenericWorkflowTransitionFields):
    source_refs: list[str] = Field(min_length=1, max_length=27)


class GenericWorkflowChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    stages: list[GenericWorkflowStageClaim] = Field(default_factory=list, max_length=30)
    transitions: list[GenericWorkflowTransitionClaim] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def validate_unique_stage_ids(self):
        ids = [stage.source_stage_id for stage in self.stages]
        if len(ids) != len(set(ids)):
            raise ValueError("generic workflow stage IDs must be unique within a source unit")
        return self


class _ProviderGenericWorkflowChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    stages: list[_ProviderGenericWorkflowStageClaim] = Field(default_factory=list, max_length=30)
    transitions: list[_ProviderGenericWorkflowTransitionClaim] = Field(default_factory=list, max_length=80)

def _provider_contract_for_unit(aliases: tuple[str, ...]):
    local_ref_enum = Enum("GenericWorkflowSourceRef", {alias: alias for alias in aliases}, type=str)
    stage = create_model(
        "ProviderGenericWorkflowStageClaimForUnit", __base__=_ProviderGenericWorkflowStageClaim,
        source_refs=(list[local_ref_enum], Field(min_length=1, max_length=len(aliases))),
    )
    transition = create_model(
        "ProviderGenericWorkflowTransitionClaimForUnit", __base__=_ProviderGenericWorkflowTransitionClaim,
        source_refs=(list[local_ref_enum], Field(min_length=1, max_length=len(aliases))),
    )
    return create_model(
        "ProviderGenericWorkflowChunkExtractionForUnit", __base__=_ProviderGenericWorkflowChunkExtraction,
        stages=(list[stage], Field(default_factory=list, max_length=30)),
        transitions=(list[transition], Field(default_factory=list, max_length=80)),
    )


def _validate_returned_aliases(response_text: str, local_refs, *, operation_id: str) -> None:
    try:
        payload = json.loads(response_text)
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    for collection_name in ("stages", "transitions"):
        collection = payload.get(collection_name)
        if not isinstance(collection, list):
            continue
        for index, claim in enumerate(collection):
            if not isinstance(claim, dict) or not isinstance(claim.get("source_refs"), list):
                continue
            resolve_local_source_references(
                claim["source_refs"], local_refs, operation_id=operation_id,
                assertion_index=index,
            )


def _resolve_provider_extraction(provider_result, local_refs, *, unit_id: str):
    stages = []
    transitions = []
    for index, claim in enumerate(provider_result.stages):
        sources = resolve_local_source_references(
            [item.value if isinstance(item, Enum) else str(item) for item in claim.source_refs],
            local_refs, operation_id=unit_id, assertion_index=index,
        )
        stages.append(GenericWorkflowStageClaim.model_validate(
            claim.model_dump(mode="json", exclude={"source_refs"})
            | {
                "source_stage_id": _application_stage_id(unit_id, index + 1),
                "source_section_ids": sources,
            }
        ))
    for index, claim in enumerate(provider_result.transitions):
        sources = resolve_local_source_references(
            [item.value if isinstance(item, Enum) else str(item) for item in claim.source_refs],
            local_refs, operation_id=unit_id, assertion_index=index,
        )
        transitions.append(GenericWorkflowTransitionClaim.model_validate(
            claim.model_dump(mode="json", exclude={"source_refs"}) | {"source_section_ids": sources}
        ))
    return GenericWorkflowChunkExtraction(
        contract_version=provider_result.contract_version, stages=stages, transitions=transitions,
    )


class GenericUnmappedStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: GenericWorkflowStageClaim
    reason: str = Field(min_length=1, max_length=240)
    status: Literal["needs_review"] = "needs_review"


class GenericUnmappedTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: GenericWorkflowTransitionClaim
    reason: str = Field(min_length=1, max_length=240)
    status: Literal["needs_review"] = "needs_review"


@dataclass(frozen=True)
class GenericWorkflowAssembly:
    workflow: CompactWorkflowExtraction
    unmapped_stages: tuple[GenericUnmappedStage, ...]
    unmapped_transitions: tuple[GenericUnmappedTransition, ...]


class GenericWorkflowExtractionError(ValueError):
    """A generic workflow unit failed provider or structured-output validation."""


@dataclass(frozen=True)
class GenericWorkflowExtractionResult:
    assembly: GenericWorkflowAssembly
    audits: tuple[object, ...]


class GenericWorkflowExtractor:
    """Extract workflow claims from source chunks without assigning semantic owners."""

    def __init__(self, generate):
        self._generate = generate
        self.last_audits: tuple[object, ...] = ()
        self.current_phase = "idle"

    def extract(
        self,
        units: tuple[GenericExtractionUnit, ...] | list[GenericExtractionUnit],
        global_context: dict | None = None,
        *,
        on_unit_success: Callable[[GenericExtractionUnit], None] | None = None,
    ) -> GenericWorkflowExtractionResult:
        from app.modules.sales_playbook.extraction import _section_audit

        if not units:
            raise GenericWorkflowExtractionError("source has no generic workflow units")
        self.last_audits = ()
        context = global_context or {}
        results: list[tuple[GenericExtractionUnit, GenericWorkflowChunkExtraction]] = []
        audits = []
        for unit in units:
            operation = f"sales_playbook.generic_workflow.{unit.unit_id}"
            self.current_phase = f"generic_workflow_extraction:{unit.unit_id}"
            local_refs = build_local_source_reference_map(unit.source_sections)
            aliases = tuple(reference.alias for reference in local_refs)
            contract = _provider_contract_for_unit(aliases)
            prompt = _generic_workflow_prompt(unit, context)
            response = self._generate(
                prompt,
                contract_response_format(contract),
                _generic_workflow_system_prompt(contract),
                operation,
            )
            audit = _section_audit(
                kind=f"generic_workflow.{unit.unit_id}", operation=operation,
                section_ids=tuple(section.fragment_id for section in unit.source_sections),
                generations=[response] if isinstance(response, LLMGeneration) else [],
                repair_count=0, success=False, provider_name=getattr(response, "provider", None),
                json_parsed=isinstance(getattr(response, "text", None), str),
                pydantic_valid=False, prompt_version="generic-workflow-v1",
            )
            audits.append(audit)
            self.last_audits = tuple(audits)
            if not isinstance(response, LLMGeneration):
                raise GenericWorkflowExtractionError("generic workflow response metadata is unavailable")
            envelope = response.response_envelope
            status = getattr(envelope, "http_status", None) if envelope is not None else None
            if not response.success or (status is not None and not 200 <= status < 300):
                raise GenericWorkflowExtractionError("generic workflow provider request failed")
            if not isinstance(response.text, str) or not response.text.strip():
                raise GenericWorkflowExtractionError("generic workflow returned empty structured output")
            if response.finish_reason != "stop":
                audits[-1] = _section_audit(
                    kind=f"generic_workflow.{unit.unit_id}", operation=operation,
                    section_ids=tuple(section.fragment_id for section in unit.source_sections),
                    generations=[response], repair_count=0, success=False,
                    provider_name=response.provider, json_parsed=True, pydantic_valid=True,
                    prompt_version="generic-workflow-v1",
                )
                self.last_audits = tuple(audits)
                raise GenericWorkflowExtractionError(
                    f"generic workflow finish_reason={response.finish_reason or 'missing'}; expected stop"
                )
            _validate_returned_aliases(response.text, local_refs, operation_id=operation)
            try:
                provider_result = contract.model_validate_json(response.text)
            except Exception as error:
                raise GenericWorkflowExtractionError(
                    f"generic workflow structured output is invalid ({type(error).__name__})"
                ) from error
            extraction = _resolve_provider_extraction(provider_result, local_refs, unit_id=unit.unit_id)
            owned = {section.fragment_id for section in unit.source_sections}
            for claim in (*extraction.stages, *extraction.transitions):
                if not claim.source_section_ids or not set(claim.source_section_ids) <= owned:
                    raise GenericWorkflowExtractionError(
                        "generic workflow output cited a source section outside its unit"
                    )
            audits[-1] = _section_audit(
                kind=f"generic_workflow.{unit.unit_id}",
                operation=operation,
                section_ids=tuple(dict.fromkeys(section.fragment_id for section in unit.source_sections)),
                generations=[response],
                repair_count=0,
                success=True,
                provider_name=response.provider,
                json_parsed=True,
                pydantic_valid=True,
                prompt_version="generic-workflow-v1",
            )
            self.last_audits = tuple(audits)
            results.append((unit, extraction))
            if on_unit_success is not None:
                on_unit_success(unit)

        self.current_phase = "generic_workflow_assembly"
        assembly = assemble_generic_workflow(results)
        self.current_phase = "generic_workflow_complete"
        self.last_audits = tuple(audits)
        return GenericWorkflowExtractionResult(assembly, self.last_audits)


def _generic_workflow_system_prompt(contract=GenericWorkflowChunkExtraction) -> str:
    schema = json.dumps(
        contract.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        "Extract only workflow structure explicitly supported by these source sections. "
        "Do not assign semantic owners based on chunk or heading. Preserve source stage names "
        "and condition language. Set canonical codes only when the source meaning has a "
        "clear correspondence; otherwise use null and retain the source text. Omit absent "
        "stages and transitions. Never invent workflow steps, outcomes, maturity, or defaults. "
        "Every emitted claim must cite only the opaque source_refs supplied for this operation. "
        "Never output internal source identifiers. Return JSON matching "
        f"this schema: {schema}"
    )


def _generic_workflow_prompt(unit: GenericExtractionUnit, context: dict) -> str:
    local_refs = build_local_source_reference_map(unit.source_sections)
    rendered = "\n\n".join(
        f'<source_fragment ref="{reference.alias}">\n{section.text}\n</source_fragment>'
        for reference, section in zip(local_refs, unit.source_sections, strict=True)
    )
    allowed = tuple(reference.alias for reference in local_refs)
    return (
        f"Extract workflow claims from generic source unit {unit.unit_id}. "
        f"Allowed source references: {', '.join(allowed)}. "
        f"Global context (not policy): {context}\n"
        f"<relevant_source_fragments>\n{rendered}\n</relevant_source_fragments>"
    )


def assemble_generic_workflow(
    unit_results: list[tuple[GenericExtractionUnit, GenericWorkflowChunkExtraction]],
) -> GenericWorkflowAssembly:
    """Assemble only mappable source-backed workflow claims; preserve the rest for review."""

    if not unit_results:
        raise ExtractionAssemblyError("generic workflow assembly requires source units")
    if len({unit.unit_id for unit, _ in unit_results}) != len(unit_results):
        raise ExtractionAssemblyError("generic workflow unit IDs must be unique")

    unit_sections: dict[str, set[str]] = {}
    available_order: list[str] = []
    source_order: dict[str, int] = {}
    for unit, _ in unit_results:
        section_ids = {section.fragment_id for section in unit.source_sections}
        if not section_ids:
            raise ExtractionAssemblyError("generic workflow unit has no source sections")
        unit_sections[unit.unit_id] = section_ids
        for section in unit.source_sections:
            if section.fragment_id not in source_order:
                source_order[section.fragment_id] = len(source_order)
                available_order.append(section.fragment_id)

    stage_candidates: dict[
        WorkflowStageCode, list[tuple[str, WorkflowUnitStage]]
    ] = defaultdict(list)
    transition_candidates: dict[
        tuple[WorkflowStageCode, WorkflowConditionCode],
        list[tuple[str, WorkflowUnitTransition, GenericWorkflowTransitionClaim]],
    ] = defaultdict(list)
    unmapped_stages: list[GenericUnmappedStage] = []
    unmapped_transitions: list[GenericUnmappedTransition] = []
    all_cited_ids: list[str] = []

    for unit, extraction in unit_results:
        owned = unit_sections[unit.unit_id]
        for claim in extraction.stages:
            _validate_sources(claim.source_section_ids, owned, "workflow stage")
            all_cited_ids = _ordered_union(all_cited_ids, claim.source_section_ids, source_order)
            if (
                claim.canonical_stage_code is None
                or claim.objective is None
                or claim.maturity is None
            ):
                unmapped_stages.append(GenericUnmappedStage(
                    claim=claim,
                    reason="Stage lacks an explicit canonical mapping, objective, or maturity.",
                ))
                continue
            if claim.entry_condition_text or claim.exit_condition_text:
                unmapped_stages.append(GenericUnmappedStage(
                    claim=claim,
                    reason="Stage includes source conditions that do not map to canonical condition codes.",
                ))
            stage_candidates[claim.canonical_stage_code].append((
                unit.unit_id,
                WorkflowUnitStage(
                    stage_code=claim.canonical_stage_code,
                    name=claim.source_name,
                    objective=claim.objective,
                    actions=claim.actions,
                    entry_conditions=claim.entry_conditions,
                    exit_conditions=claim.exit_conditions,
                    timing=claim.timing,
                    responsible_role=claim.responsible_role,
                    maturity=claim.maturity,
                    source_section_ids=claim.source_section_ids,
                    rules=[],
                ),
            ))

        for claim in extraction.transitions:
            _validate_sources(claim.source_section_ids, owned, "workflow transition")
            all_cited_ids = _ordered_union(all_cited_ids, claim.source_section_ids, source_order)
            if (
                claim.from_stage_code is None
                or claim.to_stage_code is None
                or claim.condition_code is None
                or claim.outcome is None
                or claim.maturity is None
            ):
                unmapped_transitions.append(GenericUnmappedTransition(
                    claim=claim,
                    reason="Transition lacks an explicit canonical stage, condition, outcome, or maturity.",
                ))
                continue
            transition_candidates[(claim.from_stage_code, claim.condition_code)].append((
                unit.unit_id,
                WorkflowUnitTransition(
                    from_stage=claim.from_stage_code,
                    to_stage=claim.to_stage_code,
                    condition=claim.condition_code,
                    outcome=claim.outcome,
                    wait_hours=claim.wait_hours,
                    description=claim.condition_text,
                    maturity=claim.maturity,
                    source_section_ids=claim.source_section_ids,
                ),
                claim,
            ))

    stages: dict[WorkflowStageCode, WorkflowUnitStage] = {}
    stage_sources: dict[WorkflowStageCode, list[str]] = {}
    conflicts: dict[WorkflowStageCode, WorkflowStageConflict] = {}
    for stage_code, candidates in stage_candidates.items():
        first_owner, first_stage = candidates[0]
        if len(candidates) == 1:
            stages[stage_code] = first_stage
            stage_sources[stage_code] = list(first_stage.source_section_ids)
            continue
        if all(_without_sources(item) == _without_sources(first_stage) for _, item in candidates[1:]):
            sources = _ordered_union(
                list(first_stage.source_section_ids),
                [source for _, item in candidates[1:] for source in item.source_section_ids],
                source_order,
            )
            stages[stage_code] = first_stage.model_copy(update={"source_section_ids": sources})
            stage_sources[stage_code] = sources
            continue
        if len(candidates) > 7:
            for owner, claim in candidates:
                unmapped_stages.append(GenericUnmappedStage(
                    claim=GenericWorkflowStageClaim(
                        source_stage_id=f"{owner}:{stage_code.value}", source_name=claim.name,
                        canonical_stage_code=claim.stage_code, objective=claim.objective,
                        actions=claim.actions, entry_conditions=claim.entry_conditions,
                        exit_conditions=claim.exit_conditions, timing=claim.timing,
                        responsible_role=claim.responsible_role, maturity=claim.maturity,
                        source_section_ids=claim.source_section_ids,
                    ),
                    reason="Too many conflicting source interpretations to represent as canonical authority.",
                ))
            continue
        conflict_candidates = []
        for index, (owner, candidate) in enumerate(candidates, start=1):
            compact = _compact_stage(candidate)
            conflict_candidates.append(WorkflowConflictCandidate(
                candidate_id=f"{owner}:{stage_code.value}:{index}",
                owner=owner,
                stage=compact,
                source_section_ids=list(candidate.source_section_ids),
            ))
        conflicts[stage_code] = WorkflowStageConflict(
            conflict_id=f"workflow-stage:{stage_code.value}",
            stage_code=stage_code,
            conflicting_fields=[
                field for field in _without_sources(first_stage)
                if any(_without_sources(item).get(field) != _without_sources(first_stage).get(field)
                       for _, item in candidates[1:])
            ] or ["source_interpretation"],
            candidates=conflict_candidates,
        )

    transitions: dict[tuple[WorkflowStageCode, WorkflowConditionCode], WorkflowUnitTransition] = {}
    transition_sources: dict[tuple[WorkflowStageCode, WorkflowConditionCode], list[str]] = {}
    for key, candidates in transition_candidates.items():
        source_stage, condition = key
        if source_stage in conflicts:
            for _owner, _item, claim in candidates:
                unmapped_transitions.append(GenericUnmappedTransition(
                    claim=claim,
                    reason="Transition depends on an unresolved source-stage conflict.",
                ))
            continue
        _, first, _ = candidates[0]
        if all(_without_sources(item) == _without_sources(first) for _, item, _ in candidates[1:]):
            sources = _ordered_union(
                list(first.source_section_ids),
                [source for _, item, _ in candidates[1:] for source in item.source_section_ids],
                source_order,
            )
            transitions[key] = first.model_copy(update={"source_section_ids": sources})
            transition_sources[key] = sources
            continue
        for _owner, _transition, claim in candidates:
            unmapped_transitions.append(GenericUnmappedTransition(
                claim=claim,
                reason=f"Conflicting source-backed destinations for {source_stage.value} / {condition.value}.",
            ))

    present_stage_codes = set(stages)
    for key, candidate in list(transitions.items()):
        candidates = transition_candidates[key]
        if candidate.to_stage not in present_stage_codes or candidate.from_stage not in present_stage_codes:
            del transitions[key]
            transition_sources.pop(key, None)
            for _owner, _transition, claim in candidates:
                unmapped_transitions.append(GenericUnmappedTransition(
                    claim=claim,
                    reason="Transition references a stage not independently supported by a source claim.",
                ))
            continue

    compact = CompactWorkflowExtraction(
        section_ids=all_cited_ids or available_order,
        stages=[_compact_stage(stages[code]) for code in WorkflowStageCode if code in stages],
        transitions=[_compact_transition(item) for item in transitions.values()],
        conflicts=list(conflicts.values()),
        # Generic sources have no Tu Web MX-required condition list. Their
        # missing/unmapped relations are represented as generic completeness findings.
        missing_required_conditions=[],
    )
    return GenericWorkflowAssembly(
        workflow=compact,
        unmapped_stages=tuple(unmapped_stages),
        unmapped_transitions=tuple(unmapped_transitions),
    )


def _validate_sources(claim_ids: list[str], owned: set[str], label: str) -> None:
    if not claim_ids or len(claim_ids) != len(set(claim_ids)):
        raise ExtractionAssemblyError(f"{label} has invalid source provenance")
    if not set(claim_ids) <= owned:
        raise ExtractionAssemblyError(f"{label} cites a source outside its generic unit")


def _without_sources(item: WorkflowUnitStage | WorkflowUnitTransition) -> dict:
    return item.model_dump(mode="json", exclude={"source_section_ids"})


def _ordered_union(first: list[str], second: list[str], order: dict[str, int]) -> list[str]:
    return sorted(set(first) | set(second), key=order.__getitem__)


def _compact_stage(stage: WorkflowUnitStage) -> CompactWorkflowStage:
    return CompactWorkflowStage(
        stage_code=stage.stage_code,
        name=stage.name,
        objective=stage.objective,
        actions=stage.actions,
        entry_conditions=stage.entry_conditions,
        exit_conditions=stage.exit_conditions,
        timing=stage.timing,
        responsible_role=stage.responsible_role,
        rules=stage.rules,
        maturity=stage.maturity,
        source_section_ids=stage.source_section_ids,
    )


def _compact_transition(transition: WorkflowUnitTransition) -> CompactWorkflowTransition:
    return CompactWorkflowTransition(
        from_stage=transition.from_stage,
        to_stage=transition.to_stage,
        condition=transition.condition,
        outcome=transition.outcome,
        wait_hours=transition.wait_hours,
        description=transition.description,
        maturity=transition.maturity,
        source_section_ids=transition.source_section_ids,
    )


__all__ = [
    "GenericUnmappedStage",
    "GenericUnmappedTransition",
    "GenericWorkflowAssembly",
    "GenericWorkflowChunkExtraction",
    "GenericWorkflowStageClaim",
    "GenericWorkflowTransitionClaim",
    "assemble_generic_workflow",
]
