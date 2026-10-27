"""Internal contracts and deterministic mapping for stage-aware workflow extraction.

This module deliberately contains no provider calls and no commercial defaults.  It
only defines the source-section ownership map and the compact, source-traceable
contracts consumed by the later workflow assembler.
"""

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import Enum
from math import ceil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.contracts import AIContract
from app.ai.validation import contract_response_format
from app.modules.sales_playbook.extraction import (
    CompactWorkflowExtraction,
    CompactWorkflowStage,
    CompactWorkflowTransition,
    ExtractionAssemblyError,
    MarkdownSection,
    WorkflowConflictCandidate,
    WorkflowConflictTransition,
    WorkflowStageConflict,
    map_markdown_sections,
)
from app.modules.sales_playbook.schemas import (
    PlaybookMaturity,
    WorkflowConditionCode,
    WorkflowOutcome,
    WorkflowStageCode,
    WorkflowUnitRule,
)


class WorkflowUnitKind(str, Enum):
    LEAD_FOUNDATION = "lead_foundation"
    TOUCH_1 = "touch_1"
    BRANCH_ROUTING = "branch_routing"
    TOUCH_2_3 = "touch_2_3"
    CALL_FLOW = "call_flow"
    ANALYSIS_CLOSE = "analysis_close"
    FOLLOWUP_GOVERNANCE = "followup_governance"


class WorkflowUnitSpec(BaseModel):
    """Deterministic ownership of source sections by one workflow unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: WorkflowUnitKind
    section_ids: tuple[str, ...] = Field(..., min_length=1, max_length=27)


# Descriptive compatibility alias for callers that name the extraction
# projection after its containing stage.
WorkflowUnitStageRule = WorkflowUnitRule


class WorkflowUnitStage(BaseModel):
    """Source-derived stage fields used by the existing compact workflow IR."""

    model_config = ConfigDict(extra="forbid")

    stage_code: WorkflowStageCode
    name: str = Field(..., min_length=1, max_length=120)
    objective: str = Field(..., min_length=1, max_length=500)
    actions: list[str] = Field(..., max_length=8)
    entry_conditions: list[WorkflowConditionCode] = Field(..., max_length=10)
    exit_conditions: list[WorkflowConditionCode] = Field(..., max_length=10)
    timing: str | None = Field(default=None, max_length=300)
    responsible_role: str | None = Field(default=None, max_length=80)
    maturity: PlaybookMaturity
    source_section_ids: list[str] = Field(..., min_length=1, max_length=27)
    rules: list[WorkflowUnitRule] = Field(
        default_factory=list,
        max_length=20,
        description="Emit only rules supported by this unit's source; an absent rule is UNSPECIFIED, not permission.",
    )


class WorkflowUnitTransition(BaseModel):
    """Source-derived transition fields with local source traceability."""

    model_config = ConfigDict(extra="forbid")

    from_stage: WorkflowStageCode
    to_stage: WorkflowStageCode
    condition: WorkflowConditionCode
    outcome: WorkflowOutcome
    wait_hours: int | None = Field(default=None, ge=0, le=8760)
    description: str | None = Field(default=None, max_length=500)
    maturity: PlaybookMaturity
    source_section_ids: list[str] = Field(..., min_length=1, max_length=27)


class WorkflowUnitExtraction(AIContract):
    """Provider-neutral extraction payload for one mapped workflow unit."""

    contract_version: Literal["1.0"] = "1.0"
    stages: list[WorkflowUnitStage] = Field(default_factory=list, max_length=13)
    transitions: list[WorkflowUnitTransition] = Field(default_factory=list, max_length=80)


@dataclass(frozen=True)
class WorkflowUnitMeasurement:
    """Repeatable measurements for one semantic unit without a tokenizer."""

    unit_kind: WorkflowUnitKind
    section_count: int
    source_bytes: int
    source_chars: int
    schema_bytes: int
    serialized_chars: int
    expected_output_tokens: int

    @property
    def kind(self) -> WorkflowUnitKind:
        """Compatibility alias for callers that name the unit field ``kind``."""

        return self.unit_kind

    @property
    def expected_output_chars(self) -> int:
        return self.serialized_chars


_WORKFLOW_SECTION_IDS: tuple[str, ...] = (
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/0-que-sobrevive-de-v1-2-1-y-que-cambia",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/el-flujo-completo-13-pasos",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-1-obtener-leads",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-2-filtrar-y-enriquecer-cada-lead",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-3-clasificar-matriz-ads-web",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/1-reglas-directivas-del-toque-1",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/2-catalogo-de-escenarios-y-scripts-ejecutables",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/2-catalogo-de-escenarios-y-scripts-ejecutables/escenario-1-perfil-oro-meta-ads-activos-fuga-de-trafico",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/2-catalogo-de-escenarios-y-scripts-ejecutables/escenario-2-redes-activas-vs-invisibilidad-o-fragmentacion-en-google",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/2-catalogo-de-escenarios-y-scripts-ejecutables/escenario-3-homonimos-y-confusion-de-identidad-en-google",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/2-catalogo-de-escenarios-y-scripts-ejecutables/escenario-4-perfil-inquilino-dependencia-de-doctoralia-marketplaces",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/2-catalogo-de-escenarios-y-scripts-ejecutables/escenario-5-web-rota-link-caido-en-google-maps",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-4-toque-1-rompe-bot-y-alertas-de-servicio-seccion-actualizada-v2-2/3-matriz-de-seleccion-rapida-para-el-prospector",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-5-toque-1-5-inmediato-post-bot",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-6-identificar-la-rama-del-flujo",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-7-toque-2-post-bot-24-48-hrs-despues-experimento-a-b",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-8-toque-3-identificar-decisor-y-agendar",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-9-llamada-pre-cierre-rescate-telefonico",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-10-llamada-de-exploracion-20-25-min-ya-agendada",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-11-analisis-completo-en-pantalla-en-la-llamada",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-12-propuesta-cierre-y-aislamiento-de-objecion",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/paso-13-seguimiento-t-n-dias-via-whatsapp",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/metricas-y-reglas-de-decision-del-bloque-de-60-30-a-30-b",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/patrones-personales-a-cuidar",
    "manual-de-prospeccion-en-frio-v2-2-tu-web-mx/plantilla-de-registro-por-lead-una-hoja-por-llamada",
)

_UNIT_SECTION_ORDINALS: tuple[tuple[WorkflowUnitKind, tuple[int, ...]], ...] = (
    (WorkflowUnitKind.LEAD_FOUNDATION, (1, 2, 4, 5, 6, 15)),
    (WorkflowUnitKind.TOUCH_1, tuple(range(7, 15))),
    (WorkflowUnitKind.BRANCH_ROUTING, (16, 17)),
    (WorkflowUnitKind.TOUCH_2_3, (18, 19)),
    (WorkflowUnitKind.CALL_FLOW, (3, 20, 21)),
    (WorkflowUnitKind.ANALYSIS_CLOSE, (22, 23)),
    (WorkflowUnitKind.FOLLOWUP_GOVERNANCE, (24, 25, 26, 27)),
)


def map_workflow_units(sections: Sequence[MarkdownSection]) -> tuple[WorkflowUnitSpec, ...]:
    """Assign the real selected workflow sections to seven stable semantic units."""

    materialized = list(sections)
    if len(materialized) != len(_WORKFLOW_SECTION_IDS):
        raise ValueError("workflow mapping requires exactly 27 selected sections")

    by_id: dict[str, MarkdownSection] = {}
    for section in materialized:
        if section.id not in _WORKFLOW_SECTION_IDS:
            raise ValueError(f"unknown workflow section: {section.id}")
        if section.id in by_id:
            raise ValueError(f"duplicate workflow section: {section.id}")
        by_id[section.id] = section

    missing = [section_id for section_id in _WORKFLOW_SECTION_IDS if section_id not in by_id]
    if missing:
        raise ValueError(f"missing workflow section: {missing[0]}")

    ordered_ids = tuple(section.id for section in sorted(by_id.values(), key=lambda item: item.order))
    if ordered_ids != _WORKFLOW_SECTION_IDS:
        raise ValueError("workflow sections must preserve document order")

    return tuple(
        WorkflowUnitSpec(
            kind=kind,
            section_ids=tuple(_WORKFLOW_SECTION_IDS[index - 1] for index in ordinals),
        )
        for kind, ordinals in _UNIT_SECTION_ORDINALS
    )


def workflow_unit_response_format() -> dict[str, object]:
    """Return the provider-neutral strict JSON Schema envelope for one unit."""

    return contract_response_format(WorkflowUnitExtraction)


def measure_workflow_units(
    source: str,
    units: Sequence[WorkflowUnitSpec],
) -> tuple[WorkflowUnitMeasurement, ...]:
    """Measure each unit's rendered source and structured-output bound."""

    sections_by_id = {section.id: section for section in map_markdown_sections(source)}
    unknown_ids = [
        section_id
        for unit in units
        for section_id in unit.section_ids
        if section_id not in sections_by_id
    ]
    if unknown_ids:
        raise ValueError(f"workflow measurement references unknown section: {unknown_ids[0]}")

    schema_payload = json.dumps(
        WorkflowUnitExtraction.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    schema_bytes = len(schema_payload.encode("utf-8"))
    serialized_chars = len(schema_payload)

    measurements: list[WorkflowUnitMeasurement] = []
    for unit in units:
        rendered_sections = [_render_source_section(sections_by_id[section_id]) for section_id in unit.section_ids]
        rendered_source = "\n\n".join(rendered_sections)
        source_chars = len(rendered_source)
        measurements.append(
            WorkflowUnitMeasurement(
                unit_kind=unit.kind,
                section_count=len(unit.section_ids),
                source_bytes=len(rendered_source.encode("utf-8")),
                source_chars=source_chars,
                schema_bytes=schema_bytes,
                serialized_chars=serialized_chars,
                expected_output_tokens=ceil(serialized_chars / 4),
            )
        )

    return tuple(measurements)


def assemble_compact_workflow(
    units: Sequence[WorkflowUnitExtraction],
    unit_specs: Sequence[WorkflowUnitSpec],
    available_section_ids: Collection[str],
) -> CompactWorkflowExtraction:
    """Assemble source-backed unit outputs into the existing compact workflow IR.

    This function only validates and merges extracted values.  Canonical stage order
    is used for stable serialization, never to synthesize missing policy.
    """

    materialized_units = list(units)
    materialized_specs = list(unit_specs)
    if len(materialized_units) != len(materialized_specs):
        raise ExtractionAssemblyError("workflow units and unit specs must have the same length")
    if not materialized_specs:
        raise ExtractionAssemblyError("workflow assembly requires at least one unit")

    available_ids = list(available_section_ids)
    if len(available_ids) != len(set(available_ids)):
        raise ExtractionAssemblyError("duplicate available workflow section")
    if any(not isinstance(section_id, str) or not section_id.strip() for section_id in available_ids):
        raise ExtractionAssemblyError("invalid available workflow section")
    available = set(available_ids)

    owners: dict[str, WorkflowUnitKind] = {}
    unit_kinds: set[WorkflowUnitKind] = set()
    owned_in_order: list[str] = []
    for spec in materialized_specs:
        if spec.kind in unit_kinds:
            raise ExtractionAssemblyError(f"duplicate workflow unit: {spec.kind.value}")
        unit_kinds.add(spec.kind)
        for section_id in spec.section_ids:
            if not section_id.strip():
                raise ExtractionAssemblyError("invalid workflow section ownership")
            if section_id not in available:
                raise ExtractionAssemblyError(f"unknown workflow section: {section_id}")
            if section_id in owners:
                raise ExtractionAssemblyError(f"workflow section has conflicting ownership: {section_id}")
            owners[section_id] = spec.kind
            owned_in_order.append(section_id)

    missing_units = set(WorkflowUnitKind) - unit_kinds
    if missing_units:
        missing = ", ".join(sorted(kind.value for kind in missing_units))
        raise ExtractionAssemblyError(f"workflow is missing unit IDs: {missing}")
    unowned = available - set(owners)
    if unowned:
        missing = ", ".join(sorted(unowned))
        raise ExtractionAssemblyError(f"workflow sections have no unit owner: {missing}")

    section_order = {section_id: index for index, section_id in enumerate(owned_in_order)}
    stage_candidates: dict[
        WorkflowStageCode,
        list[tuple[WorkflowUnitKind, WorkflowUnitStage, list[WorkflowUnitTransition]]],
    ] = {}
    transition_candidates: dict[
        tuple[WorkflowStageCode, WorkflowConditionCode],
        list[tuple[WorkflowUnitKind, WorkflowUnitTransition]],
    ] = {}

    for unit, spec in zip(materialized_units, materialized_specs, strict=True):
        if not isinstance(unit, WorkflowUnitExtraction):
            raise ExtractionAssemblyError(f"invalid extraction for workflow unit: {spec.kind.value}")
        owned_sections = set(spec.section_ids)
        normalized_stages: list[WorkflowUnitStage] = []
        for stage in unit.stages:
            _validate_element_sources(stage.source_section_ids, owned_sections, available, "stage")
            normalized_stage = stage.model_copy(
                update={
                    "rules": _merge_policy_rules(
                        [], stage.rules, owned_sections, available, section_order
                    )
                }
            )
            normalized_stages.append(normalized_stage)
            stage_candidates.setdefault(stage.stage_code, []).append((spec.kind, normalized_stage, []))

        for transition in unit.transitions:
            _validate_element_sources(transition.source_section_ids, owned_sections, available, "transition")
            key = (transition.from_stage, transition.condition)
            transition_candidates.setdefault(key, []).append((spec.kind, transition))

    stages: dict[WorkflowStageCode, WorkflowUnitStage] = {}
    stage_sources: dict[WorkflowStageCode, list[str]] = {}
    conflicts: dict[WorkflowStageCode, WorkflowStageConflict] = {}
    for stage_code, candidates in stage_candidates.items():
        _, first_stage, _ = candidates[0]
        if len(candidates) == 1:
            stages[stage_code] = first_stage
            stage_sources[stage_code] = list(first_stage.source_section_ids)
            continue

        differing_fields = [
            field for field, first_value in _stage_without_sources(first_stage).items()
            if any(_stage_without_sources(stage).get(field) != first_value for _, stage, _ in candidates[1:])
        ]
        repeated_owners = len({owner for owner, _, _ in candidates}) != len(candidates)
        if differing_fields and repeated_owners:
            raise ExtractionAssemblyError(f"conflicting workflow stage: {stage_code.value}")
        if not differing_fields:
            merged_rules = first_stage.rules
            for owner, stage, _ in candidates[1:]:
                try:
                    merged_rules = _merge_policy_rules(
                        merged_rules, stage.rules,
                        set(next(spec.section_ids for spec in materialized_specs if spec.kind == owner)),
                        available, section_order,
                    )
                except ExtractionAssemblyError:
                    if repeated_owners:
                        raise
                    differing_fields = ["rules"]
                    break
            if not differing_fields:
                merged_sources = list(first_stage.source_section_ids)
                for _, stage, _ in candidates[1:]:
                    merged_sources = _merge_section_ids(merged_sources, stage.source_section_ids, section_order)
                stages[stage_code] = first_stage.model_copy(
                    update={"rules": merged_rules, "source_section_ids": merged_sources}
                )
                stage_sources[stage_code] = merged_sources
                continue

        conflict_candidates = [
            WorkflowConflictCandidate(
                candidate_id=f"{owner.value}:{stage_code.value}:{index}",
                owner=owner.value,
                stage=_compact_unit_stage(stage),
                source_section_ids=list(stage.source_section_ids),
            )
            for index, (owner, stage, _) in enumerate(candidates, start=1)
        ]
        conflicts[stage_code] = WorkflowStageConflict(
            conflict_id=f"workflow-stage:{stage_code.value}",
            stage_code=stage_code,
            conflicting_fields=differing_fields,
            candidates=conflict_candidates,
        )

    transitions: dict[tuple[WorkflowStageCode, WorkflowConditionCode], WorkflowUnitTransition] = {}
    transition_sources: dict[tuple[WorkflowStageCode, WorkflowConditionCode], list[str]] = {}
    conflicting_transition_keys: set[tuple[WorkflowStageCode, WorkflowConditionCode]] = set()
    transition_only_conflicts: dict[
        WorkflowStageCode,
        list[tuple[tuple[WorkflowStageCode, WorkflowConditionCode], list[tuple[WorkflowUnitKind, WorkflowUnitTransition]]]],
    ] = {}
    for key, candidates in transition_candidates.items():
        _, first_transition = candidates[0]
        if len(candidates) == 1:
            transitions[key] = first_transition
            transition_sources[key] = list(first_transition.source_section_ids)
            continue
        if all(_without_sources(transition) == _without_sources(first_transition)
               for _, transition in candidates[1:]):
            source_ids = list(first_transition.source_section_ids)
            for _, transition in candidates[1:]:
                source_ids = _merge_section_ids(source_ids, transition.source_section_ids, section_order)
            transitions[key] = first_transition.model_copy(update={"source_section_ids": source_ids})
            transition_sources[key] = source_ids
            continue
        if len({owner for owner, _ in candidates}) != len(candidates):
            raise ExtractionAssemblyError("conflicting workflow transition")

        conflict = conflicts.get(key[0])
        if conflict is None:
            conflict = next((
                candidate_conflict
                for code, candidate_conflict in conflicts.items()
                if any(transition.to_stage == code for _, transition in candidates)
            ), None)
        if conflict:
            field_name = f"transitions[{key[1].value}]"
            if field_name not in conflict.conflicting_fields:
                conflict.conflicting_fields.append(field_name)
            conflicting_transition_keys.add(key)
            continue
        transition_only_conflicts.setdefault(key[0], []).append((key, candidates))

    for stage_code, key_groups in transition_only_conflicts.items():
        candidate_owners = list(dict.fromkeys(owner for _, group in key_groups for owner, _ in group))
        candidates = []
        for owner in candidate_owners:
            owner_stage = next(
                (stage for candidate_owner, stage, _ in stage_candidates.get(stage_code, [])
                 if candidate_owner is owner),
                None,
            )
            candidate_transitions = [
                WorkflowConflictTransition(
                    owner=owner.value,
                    transition=_compact_unit_transition(transition),
                    source_section_ids=list(transition.source_section_ids),
                )
                for _, group in key_groups
                for transition_owner, transition in group
                if transition_owner is owner
            ]
            source_ids: list[str] = []
            for item in candidate_transitions:
                source_ids = _merge_section_ids(source_ids, item.source_section_ids, section_order)
            candidates.append(WorkflowConflictCandidate(
                candidate_id=f"{owner.value}:{stage_code.value}",
                owner=owner.value,
                stage=_compact_unit_stage(owner_stage) if owner_stage is not None else None,
                source_section_ids=source_ids,
                transitions=candidate_transitions,
            ))
        conflicts[stage_code] = WorkflowStageConflict(
            conflict_id=f"workflow-transition:{stage_code.value}",
            stage_code=stage_code,
            conflict_type="transition",
            conflicting_fields=[f"transitions[{key[1].value}]" for key, _ in key_groups],
            candidates=candidates,
        )
        conflicting_transition_keys.update(key for key, _ in key_groups)

    # Preserve each owner's transitions alongside its candidate, never as canonical
    # authority while the stage interpretation remains unresolved.
    for stage_code, conflict in conflicts.items():
        if conflict.conflict_type == "transition":
            for key, _candidates in transition_only_conflicts[stage_code]:
                transitions.pop(key, None)
                transition_sources.pop(key, None)
            continue
        candidate_by_owner = {candidate.owner: candidate for candidate in conflict.candidates}
        for key, candidates in transition_candidates.items():
            touches_stage = key[0] == stage_code or any(
                transition.to_stage == stage_code for _, transition in candidates
            )
            if not touches_stage:
                continue
            if key in transitions:
                transitions.pop(key)
                transition_sources.pop(key, None)
            for owner, transition in candidates:
                candidate = candidate_by_owner.get(owner.value)
                if candidate is None:
                    raise ExtractionAssemblyError(
                        f"workflow transition depends on unresolved stage without a candidate: {stage_code.value}"
                    )
                candidate.transitions.append(WorkflowConflictTransition(
                    owner=owner.value,
                    transition=_compact_unit_transition(transition),
                    source_section_ids=list(transition.source_section_ids),
                ))
        for candidate in conflict.candidates:
            candidate.transitions.sort(key=lambda item: (
                section_order[item.source_section_ids[0]], item.transition.condition.value
            ))

    canonical_stages = set(WorkflowStageCode)
    unresolved_stage_codes = {
        code for code, conflict in conflicts.items() if conflict.conflict_type == "stage"
    }
    missing_stages = canonical_stages - set(stages) - unresolved_stage_codes
    if missing_stages:
        missing = ", ".join(sorted(stage.value for stage in missing_stages))
        raise ExtractionAssemblyError(f"workflow is missing source-derived stages: {missing}")

    for transition in transitions.values():
        if transition.from_stage not in canonical_stages or transition.to_stage not in canonical_stages:
            raise ExtractionAssemblyError("workflow transition references an unknown stage")

    required_conditions = {
        WorkflowConditionCode.BOT_RESPONSE,
        WorkflowConditionCode.HUMAN_FIRST,
        WorkflowConditionCode.WHATSAPP_NOT_SCHEDULED,
        WorkflowConditionCode.NEGATIVE_RESPONSE,
        WorkflowConditionCode.CLEAR_INTEREST,
        WorkflowConditionCode.EXPLORATION_COMPLETED,
    }
    unresolved_transition_conditions = {
        condition for _, condition in conflicting_transition_keys
    }
    missing_conditions = required_conditions - {transition.condition for transition in transitions.values()}
    missing_conditions -= unresolved_transition_conditions
    if missing_conditions and not conflicts:
        missing = ", ".join(sorted(condition.value for condition in missing_conditions))
        raise ExtractionAssemblyError(f"workflow is missing required conditional transitions: {missing}")

    compact_stages = [
        CompactWorkflowStage(
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
        )
        for stage_code in WorkflowStageCode
        if stage_code in stages
        for stage in [stages[stage_code]]
    ]
    compact_transitions = [
        CompactWorkflowTransition(
            from_stage=transition.from_stage,
            to_stage=transition.to_stage,
            condition=transition.condition,
            outcome=transition.outcome,
            wait_hours=transition.wait_hours,
            description=transition.description,
            maturity=transition.maturity,
        )
        for transition in transitions.values()
    ]
    effective_section_ids: list[str] = []
    for source_ids in (*stage_sources.values(), *transition_sources.values()):
        effective_section_ids = _merge_section_ids(effective_section_ids, source_ids, section_order)
    for conflict in conflicts.values():
        for candidate in conflict.candidates:
            if candidate.source_section_ids:
                effective_section_ids = _merge_section_ids(
                    effective_section_ids, candidate.source_section_ids, section_order
                )
            for transition in candidate.transitions:
                effective_section_ids = _merge_section_ids(
                    effective_section_ids, transition.source_section_ids, section_order
                )
    return CompactWorkflowExtraction(
        section_ids=effective_section_ids,
        stages=compact_stages,
        transitions=compact_transitions,
        conflicts=list(conflicts.values()),
        missing_required_conditions=sorted(missing_conditions, key=lambda condition: condition.value),
    )


def _compact_unit_stage(stage: WorkflowUnitStage) -> CompactWorkflowStage:
    return CompactWorkflowStage(
        stage_code=stage.stage_code, name=stage.name, objective=stage.objective,
        actions=stage.actions, entry_conditions=stage.entry_conditions,
        exit_conditions=stage.exit_conditions, timing=stage.timing,
        responsible_role=stage.responsible_role, rules=stage.rules, maturity=stage.maturity,
        source_section_ids=stage.source_section_ids,
    )


def _compact_unit_transition(transition: WorkflowUnitTransition) -> CompactWorkflowTransition:
    return CompactWorkflowTransition(
        from_stage=transition.from_stage, to_stage=transition.to_stage,
        condition=transition.condition, outcome=transition.outcome,
        wait_hours=transition.wait_hours, description=transition.description,
        maturity=transition.maturity, source_section_ids=transition.source_section_ids,
    )


def _validate_element_sources(
    source_section_ids: Sequence[str],
    owned_sections: set[str],
    available_sections: set[str],
    element_kind: str,
) -> None:
    if not source_section_ids or any(not section_id.strip() for section_id in source_section_ids):
        raise ExtractionAssemblyError(f"{element_kind} has invalid provenance")
    if len(source_section_ids) != len(set(source_section_ids)):
        raise ExtractionAssemblyError(f"{element_kind} has duplicate provenance")
    unknown = [section_id for section_id in source_section_ids if section_id not in available_sections]
    if unknown:
        raise ExtractionAssemblyError(f"{element_kind} references unknown source section: {unknown[0]}")
    foreign = [section_id for section_id in source_section_ids if section_id not in owned_sections]
    if foreign:
        raise ExtractionAssemblyError(f"{element_kind} source section is not owned by workflow unit: {foreign[0]}")


def _merge_policy_rules(
    first: Sequence[WorkflowUnitRule],
    second: Sequence[WorkflowUnitRule],
    owned_sections: set[str],
    available_sections: set[str],
    section_order: dict[str, int],
) -> list[WorkflowUnitRule]:
    """Merge source-backed rules without resolving commercial conflicts."""

    for rule in second:
        _validate_element_sources(rule.source_section_ids, owned_sections, available_sections, "rule")
    merged: dict[tuple[object, object], WorkflowUnitRule] = {}
    for rule in (*first, *second):
        identity = (rule.code, rule.condition)
        previous = merged.get(identity)
        if previous is None:
            merged[identity] = rule
            continue
        if previous.effect is not rule.effect:
            raise ExtractionAssemblyError(
                f"conflicting workflow rule effect: {rule.code.value}"
            )
        if previous.maturity is not rule.maturity:
            raise ExtractionAssemblyError(
                f"conflicting workflow rule maturity: {rule.code.value}"
            )
        if previous.model_dump(mode="json", exclude={"source_section_ids"}) != rule.model_dump(
            mode="json", exclude={"source_section_ids"}
        ):
            raise ExtractionAssemblyError(
                f"conflicting workflow rule definition: {rule.code.value}"
            )
        merged[identity] = previous.model_copy(
            update={
                "source_section_ids": _merge_section_ids(
                    previous.source_section_ids,
                    rule.source_section_ids,
                    section_order,
                )
            }
        )
    return list(merged.values())


def _without_sources(value: WorkflowUnitStage | WorkflowUnitTransition) -> dict[str, object]:
    return value.model_dump(mode="json", exclude={"source_section_ids"})


def _stage_without_sources(value: WorkflowUnitStage) -> dict[str, object]:
    payload = value.model_dump(mode="json", exclude={"source_section_ids", "rules"})
    return payload


def _merge_section_ids(
    first: Sequence[str],
    second: Sequence[str],
    section_order: dict[str, int],
) -> list[str]:
    return sorted(set(first) | set(second), key=section_order.__getitem__)


def _render_source_section(section: MarkdownSection) -> str:
    heading = f"{'#' * max(section.level, 1)} {section.heading}"
    body = section.text.strip()
    return f"{heading}\n{body}" if body else heading
