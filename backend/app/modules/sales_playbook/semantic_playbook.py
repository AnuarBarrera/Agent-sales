"""Versioned semantic draft assembly; source entailment remains a human review gate."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    model_validator,
)

from app.modules.sales_playbook.extraction import (
    WorkflowConflictCandidate,
    WorkflowConflictResolution,
    WorkflowStageConflict,
    expand_workflow,
)
from app.modules.sales_playbook.generator import ManualSemanticExtractionResult
from app.modules.sales_playbook.generic_facts import GenericSourceClaim
from app.modules.sales_playbook.generic_relations import GenericWorkflowGraphV1
from app.modules.sales_playbook.generic_workflow import (
    GenericUnmappedStage,
    GenericUnmappedTransition,
)
from app.modules.sales_playbook.schemas import (
    REQUIRED_WORKFLOW_CONDITIONS,
    SEMANTIC_PLAYBOOK_CONTRACT_VERSION,
    ChannelPolicy,
    ClassificationRule,
    CloserPolicy,
    ConditionRule,
    DiscoveryQuestion,
    HumanReviewedProvenance,
    HumanReviewedWorkflowTransition,
    ObjectionRule,
    PendingDecision,
    PlaybookExperiment,
    PlaybookOrigin,
    PlaybookProvenance,
    PlaybookTemplate,
    PolicyRule,
    ProspectingPolicy,
    ReviewableWorkflowPolicy,
    SalesPlaybookContent,
    SalesPlaybookWorkflowTransitionComplete,
    SetterPolicy,
    StrategyPolicy,
    TargetSegment,
    WorkflowConditionCode,
    WorkflowPolicy,
    WorkflowStage,
    WorkflowStageCode,
    WorkflowStageRule,
    WorkflowTransition,
)
from app.modules.sales_playbook.semantic_policy import (
    CompilationStatus,
    MaturityEvidence,
    SemanticCompilation,
    SemanticPolicyAssertion,
    UnresolvedSemanticPolicy,
    compile_semantic_policies,
    resolve_semantic_maturity,
)

# Only this explicitly bounded source scope has an unambiguous workflow stage.
# Other source backed rules survive as unresolved assertions for review.
_PLACEMENT_SCOPE = {
    "touch 1": WorkflowStageCode.TOUCH_1,
    "toque 1": WorkflowStageCode.TOUCH_1,
    "touch 1 before the authorized commercial point": WorkflowStageCode.TOUCH_1,
    "toque 1 antes del punto comercial autorizado": WorkflowStageCode.TOUCH_1,
    "touch 1 before product disclosure": WorkflowStageCode.TOUCH_1,
    "toque 1, antes de revelar el producto": WorkflowStageCode.TOUCH_1,
}


def workflow_stage_from_candidate(candidate: WorkflowConflictCandidate) -> WorkflowStage | None:
    """Expand a retained candidate exactly; never combine candidate values."""
    if candidate.stage is None:
        return None
    source = PlaybookProvenance(
        origin=PlaybookOrigin.MANUAL, source_ref=",".join(candidate.source_section_ids),
    )
    return WorkflowStage(
        code=candidate.stage.stage_code,
        step_number=list(WorkflowStageCode).index(candidate.stage.stage_code) + 1,
        name=candidate.stage.name,
        objective=candidate.stage.objective,
        actions=candidate.stage.actions,
        entry_conditions=candidate.stage.entry_conditions,
        exit_conditions=candidate.stage.exit_conditions,
        timing=candidate.stage.timing,
        responsible_role=candidate.stage.responsible_role,
        rules=[WorkflowStageRule(
            code=rule.code, title=rule.title, description=rule.description,
            applies_to=rule.applies_to, priority=rule.priority, effect=rule.effect,
            condition=rule.condition, maturity=rule.maturity,
            provenance=PlaybookProvenance(
                origin=PlaybookOrigin.MANUAL,
                source_ref=",".join(rule.source_section_ids),
            ),
        ) for rule in candidate.stage.rules],
        maturity=candidate.stage.maturity,
        provenance=source,
    )


def workflow_transitions_from_candidate(candidate: WorkflowConflictCandidate) -> list[WorkflowTransition]:
    return [WorkflowTransition(
        from_stage=item.transition.from_stage,
        to_stage=item.transition.to_stage,
        condition=item.transition.condition,
        outcome=item.transition.outcome,
        wait_hours=item.transition.wait_hours,
        description=item.transition.description,
        maturity=item.transition.maturity,
        provenance=PlaybookProvenance(
            origin=PlaybookOrigin.MANUAL, source_ref=",".join(item.source_section_ids),
        ),
    ) for item in candidate.transitions]


def resolve_semantic_workflow_conflict(
    content: SalesPlaybookContentV3,
    *,
    conflict_id: str,
    candidate_id: str,
    reviewer_id: str,
) -> SalesPlaybookContentV3:
    """Apply one explicit candidate selection to a v3 DRAFT content snapshot."""
    conflicts = list(content.workflow_conflicts)
    index = next((i for i, item in enumerate(conflicts) if item.conflict_id == conflict_id), None)
    if index is None:
        raise ValueError("Workflow conflict not found")
    conflict = conflicts[index]
    if conflict.status != "needs_review":
        raise ValueError("Workflow conflict is already resolved")
    candidate = next((item for item in conflict.candidates if item.candidate_id == candidate_id), None)
    if candidate is None:
        raise ValueError("Workflow conflict candidate not found")

    stages = list(content.workflow.stages)
    selected_stage = workflow_stage_from_candidate(candidate)
    if conflict.conflict_type == "stage":
        if selected_stage is None or selected_stage.code is not conflict.stage_code:
            raise ValueError("Selected candidate has no matching source-backed stage")
        if any(stage.code is conflict.stage_code for stage in stages):
            raise ValueError("Conflicted stage already has canonical authority")
        stages.append(selected_stage)
        stages.sort(key=lambda stage: stage.step_number)

    selected_transitions = workflow_transitions_from_candidate(candidate)
    transitions = list(content.workflow.transitions)
    existing_keys = {(item.from_stage, item.condition) for item in transitions}
    selected_keys = [(item.from_stage, item.condition) for item in selected_transitions]
    if len(selected_keys) != len(set(selected_keys)) or existing_keys.intersection(selected_keys):
        raise ValueError("Selected candidate transitions conflict with canonical workflow")
    transitions.extend(selected_transitions)
    transitions.sort(key=lambda item: (
        list(WorkflowStageCode).index(item.from_stage),
        list(WorkflowConditionCode).index(item.condition),
    ))

    resolution = WorkflowConflictResolution(
        candidate_id=candidate.candidate_id,
        reviewer_id=reviewer_id,
        action="select_candidate",
        resolved_at=datetime.now(UTC),
    )
    conflicts[index] = conflict.model_copy(update={
        "status": "resolved",
        "resolved_candidate_id": candidate.candidate_id,
        "resolution": resolution,
    })

    policy = content.semantic_policy.model_copy(deep=True)
    compiled_rules = list(policy.compilation.compiled_rules)
    unresolved = []
    placements = list(policy.placements)
    for item in policy.compilation.unresolved:
        mapped_stage = None
        scopes = {assertion.scope_text.casefold().strip()
                  for assertion in item.assertions if assertion.scope_text}
        if len(scopes) == 1:
            mapped_stage = _PLACEMENT_SCOPE.get(next(iter(scopes)))
        if (conflict.conflict_type == "stage" and mapped_stage is conflict.stage_code
                and item.reason == "workflow stage conflict requires human resolution"):
            result = compile_semantic_policies(item.assertions)
            compiled_rules.extend(result.compiled_rules)
            unresolved.extend(result.unresolved)
            for trace in result.compiled_rules:
                placements.append(CompiledRulePlacement(
                    stage_code=conflict.stage_code,
                    source_assertion_ids=trace.source_assertion_ids,
                ))
                selected_stage.rules.append(trace.rule)
        else:
            unresolved.append(item)
    policy.compilation.compiled_rules = compiled_rules
    policy.compilation.unresolved = unresolved
    policy.placements = placements
    policy.workflow_conflicted_stage_codes = [
        code for code in policy.workflow_conflicted_stage_codes if code is not conflict.stage_code
    ]
    # A human workflow edit changes the authority surface. Any earlier
    # acceptance of semantic findings must be reviewed again against it.
    policy.workflow_reviews = []

    missing_conditions = sorted(
        REQUIRED_WORKFLOW_CONDITIONS - {item.condition for item in transitions}, key=lambda item: item.value,
    )
    return SalesPlaybookContentV3.model_validate(content.model_dump(mode="python") | {
        "workflow": content.workflow.model_copy(update={"stages": stages, "transitions": transitions}),
        "workflow_conflicts": conflicts,
        "workflow_missing_required_conditions": missing_conditions,
        "semantic_policy": policy,
    })


def complete_required_workflow_transition(
    content: SalesPlaybookContentV3,
    *,
    transition: SalesPlaybookWorkflowTransitionComplete,
    reviewer_id: str,
) -> SalesPlaybookContentV3:
    """Apply an explicit typed human completion without fabricating source origin."""
    if any(item.status == "needs_review" for item in content.workflow_conflicts):
        raise ValueError("Resolve workflow conflicts before completing required transitions")
    if transition.condition not in content.workflow_missing_required_conditions:
        raise ValueError("Workflow condition is not currently missing")
    known_stages = {stage.code for stage in content.workflow.stages}
    if transition.from_stage not in known_stages or transition.to_stage not in known_stages:
        raise ValueError("Human transition references an unavailable workflow stage")
    known_sections = set(content.semantic_policy.source_section_ids)
    if not set(transition.source_section_ids) <= known_sections:
        raise ValueError("Human transition references unknown source sections")

    new_transition = HumanReviewedWorkflowTransition(
        from_stage=transition.from_stage,
        to_stage=transition.to_stage,
        condition=transition.condition,
        outcome=transition.outcome,
        wait_hours=transition.wait_hours,
        description=transition.description,
        maturity=transition.maturity,
        provenance=HumanReviewedProvenance(
            source_ref=",".join(transition.source_section_ids) or None,
            note=f"Human-supplied by reviewer {reviewer_id} at {datetime.now(UTC).isoformat()}",
            decision_type="source_interpretation" if transition.source_section_ids else "business_decision",
            reviewer_id=reviewer_id,
            reviewed_at=datetime.now(UTC),
        ),
    )
    if any(
        item.from_stage == new_transition.from_stage and item.condition == new_transition.condition
        for item in content.workflow.transitions
    ):
        raise ValueError("Human transition duplicates an existing workflow relationship")

    data = content.model_dump(mode="json")
    transitions = [*data["workflow"]["transitions"], new_transition.model_dump(mode="json")]
    transitions.sort(key=lambda item: (
        list(WorkflowStageCode).index(WorkflowStageCode(item["from_stage"])),
        list(WorkflowConditionCode).index(WorkflowConditionCode(item["condition"])),
    ))
    data["workflow"]["transitions"] = transitions
    remaining = REQUIRED_WORKFLOW_CONDITIONS - {WorkflowConditionCode(item["condition"]) for item in transitions}
    data["workflow_missing_required_conditions"] = sorted(item.value for item in remaining)
    data["semantic_policy"]["workflow_reviews"] = []
    completed = SalesPlaybookContentV3.model_validate(data)
    if not remaining:
        validate_approved_workflow_structure(completed.workflow)
    return completed


def validate_approved_workflow_structure(workflow: ReviewableWorkflowPolicy) -> None:
    """Run the canonical validator without widening legacy extraction enums."""
    data = workflow.model_dump(mode="json")
    for transition in data["transitions"]:
        if transition["provenance"]["origin"] == "human_reviewed":
            # Origin is audit metadata. This validation-only projection preserves
            # structure while keeping provider-facing v1/v2 enums unchanged.
            transition["provenance"]["origin"] = "unspecified"
            transition["provenance"].pop("decision_type", None)
            transition["provenance"].pop("reviewer_id", None)
            transition["provenance"].pop("reviewed_at", None)
    WorkflowPolicy.model_validate(data)


class CompiledRulePlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_code: WorkflowStageCode
    source_assertion_ids: list[str] = Field(min_length=1)


class SemanticWorkflowReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str = Field(min_length=1, max_length=180)
    action: Literal["accept_current_interpretation"]
    reviewer_id: str = Field(min_length=1, max_length=64)
    reviewed_at: datetime


class SemanticPlaybookPolicy(BaseModel):
    """All source assertions remain visible, including unresolved normalization."""

    model_config = ConfigDict(extra="forbid")

    assertions: list[SemanticPolicyAssertion]
    compilation: SemanticCompilation
    placements: list[CompiledRulePlacement]
    source_section_ids: list[str] = Field(min_length=1)
    source_review_required: Literal[True] = True
    extraction_complete: bool
    extraction_truncated: bool = False
    reviewed_guardrail_assertion_ids: list[str] = Field(default_factory=list)
    rejected_assertion_ids: list[str] = Field(default_factory=list)
    safety_findings: list[str] = Field(default_factory=list)
    workflow_conflicted_stage_codes: list[WorkflowStageCode] = Field(default_factory=list)
    workflow_unmapped_stage_codes: list[WorkflowStageCode] = Field(default_factory=list)
    workflow_reviews: list[SemanticWorkflowReview] = Field(default_factory=list, max_length=100)

    @field_serializer("compilation")
    def serialize_compilation(self, value: SemanticCompilation):
        # The prototype's derived assertion_ids property is not an input field.
        # Exclude it so persisted JSON round-trips through the strict v3 schema.
        return value.model_dump(mode="json", exclude={
            "unresolved": {"__all__": {"assertion_ids"}},
        })

    @model_validator(mode="after")
    def validate_review_ids(self):
        finding_ids = [item.finding_id for item in self.workflow_reviews]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("duplicate semantic workflow review")
        ids = [item.assertion_id for item in self.assertions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source assertion ID")
        if self.compilation.input_assertion_ids != ids:
            raise ValueError("compilation does not account for source assertions")
        known_sections = set(self.source_section_ids)
        if len(known_sections) != len(self.source_section_ids):
            raise ValueError("duplicate source section provenance")
        by_id = {item.assertion_id: item for item in self.assertions}
        for item in self.assertions:
            if not set(item.source_section_ids) <= known_sections:
                raise ValueError("source assertion has foreign provenance")
            if not set(item.maturity_source_section_ids) <= known_sections:
                raise ValueError("maturity has foreign governance provenance")
        accounted = []
        for trace in self.compilation.compiled_rules:
            if any(item not in by_id for item in trace.source_assertion_ids):
                raise ValueError("compiled trace references unknown source assertion")
            source_ids = {
                section for assertion_id in trace.source_assertion_ids
                for section in by_id[assertion_id].source_section_ids
            }
            governance_ids = {
                section for assertion_id in trace.source_assertion_ids
                for section in by_id[assertion_id].maturity_source_section_ids
            }
            if set(trace.source_section_ids) != source_ids or set(trace.maturity_source_section_ids) != governance_ids:
                raise ValueError("compiled trace has inconsistent provenance")
            verified = compile_semantic_policies(
                [by_id[assertion_id] for assertion_id in trace.source_assertion_ids]
            )
            if (len(verified.compiled_rules) != 1
                    or verified.compiled_rules[0] != trace):
                raise ValueError("stored rule differs from deterministic compilation")
            accounted.extend(trace.source_assertion_ids)
        for unresolved in self.compilation.unresolved:
            if any(item.assertion_id not in by_id for item in unresolved.assertions):
                raise ValueError("unresolved trace references unknown assertion")
            accounted.extend(item.assertion_id for item in unresolved.assertions)
        if len(accounted) != len(ids) or set(accounted) != set(ids):
            raise ValueError("each source assertion needs exactly one compilation outcome")
        detected_conflicts = {
            assertion_id
            for unresolved in compile_semantic_policies(self.assertions).unresolved
            if unresolved.status is CompilationStatus.CONFLICT
            for assertion_id in unresolved.assertion_ids
        }
        reported_conflicts = {
            assertion_id
            for unresolved in self.compilation.unresolved
            if unresolved.status is CompilationStatus.CONFLICT
            for assertion_id in unresolved.assertion_ids
        }
        if detected_conflicts != reported_conflicts:
            raise ValueError("stored compilation conceals a semantic conflict")
        deterministic = compile_semantic_policies(self.assertions)
        expected_rules = {
            tuple(trace.source_assertion_ids): trace for trace in deterministic.compiled_rules
        }
        expected_unresolved = {
            tuple(item.assertion_ids): item for item in deterministic.unresolved
        }
        stored_outcomes = set()
        for trace in self.compilation.compiled_rules:
            key = tuple(trace.source_assertion_ids)
            if expected_rules.get(key) != trace:
                raise ValueError("stored rule differs from deterministic compilation")
            stored_outcomes.add(key)
        for unresolved in self.compilation.unresolved:
            key = tuple(unresolved.assertion_ids)
            expected = expected_unresolved.get(key)
            if expected is None and key in expected_rules:
                contributors = [by_id[assertion_id] for assertion_id in key]
                scopes = {item.scope_text.casefold().strip() for item in contributors if item.scope_text}
                stage_code = _PLACEMENT_SCOPE.get(next(iter(scopes))) if len(scopes) == 1 else None
                if stage_code in self.workflow_conflicted_stage_codes:
                    expected = UnresolvedSemanticPolicy(
                        assertions=contributors, status=CompilationStatus.AMBIGUOUS,
                        reason="workflow stage conflict requires human resolution",
                        source_section_ids=list(dict.fromkeys(
                            section for item in contributors for section in item.source_section_ids
                        )),
                    )
                elif stage_code in self.workflow_unmapped_stage_codes:
                    expected = UnresolvedSemanticPolicy(
                        assertions=contributors, status=CompilationStatus.AMBIGUOUS,
                        reason="source-backed policy references a stage not mapped in the workflow",
                        source_section_ids=list(dict.fromkeys(
                            section for item in contributors for section in item.source_section_ids
                        )),
                    )
                elif len(scopes) != 1 or next(iter(scopes)) not in _PLACEMENT_SCOPE:
                    expected = UnresolvedSemanticPolicy(
                        assertions=contributors, status=CompilationStatus.AMBIGUOUS,
                        reason="executable stage placement is unresolved",
                        source_section_ids=list(dict.fromkeys(
                            section for item in contributors for section in item.source_section_ids
                        )),
                    )
            if unresolved != expected:
                raise ValueError("stored unresolved outcome differs from deterministic compilation")
            stored_outcomes.add(key)
        if stored_outcomes != set(expected_rules) | set(expected_unresolved):
            raise ValueError("stored compilation omits a deterministic outcome")
        compiled_ids = {
            assertion_id
            for trace in self.compilation.compiled_rules
            for assertion_id in trace.source_assertion_ids
        }
        if len(self.reviewed_guardrail_assertion_ids) != len(set(self.reviewed_guardrail_assertion_ids)):
            raise ValueError("duplicate reviewed guardrail assertion ID")
        if not set(self.reviewed_guardrail_assertion_ids) <= compiled_ids:
            raise ValueError("reviewed IDs must belong to compiled guardrails")
        if set(self.reviewed_guardrail_assertion_ids) & set(self.rejected_assertion_ids):
            raise ValueError("rejected assertions cannot be approved guardrails")
        if not set(self.rejected_assertion_ids) <= set(ids):
            raise ValueError("rejected IDs must belong to source assertions")
        return self


class SourceDocumentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=64)
    filename: str = Field(min_length=1, max_length=180)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceSectionReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    extraction_section_id: str = Field(min_length=1, max_length=512)
    document_id: str | None = Field(default=None, max_length=64)
    original_section_id: str = Field(min_length=1, max_length=512)
    filename: str = Field(min_length=1, max_length=180)
    heading_path: list[str] = Field(default_factory=list, max_length=20)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    fragment_id: str | None = Field(default=None, max_length=80)
    start_byte_offset: int | None = Field(default=None, ge=0)
    end_byte_offset: int | None = Field(default=None, ge=0)


class SourceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: Literal["seller_profile", "source_document", "pasted_document"]
    label: str = Field(min_length=1, max_length=180)
    value: str = Field(min_length=1, max_length=1000)
    source_section_ids: list[str] = Field(default_factory=list, max_length=100)


class SourceConflictReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: str = Field(min_length=1, max_length=120)
    field: str = Field(min_length=1, max_length=80)
    claims: list[SourceClaim] = Field(min_length=2, max_length=10)
    status: Literal["needs_review"] = "needs_review"


class CompletenessStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    CONFLICTED = "conflicted"


class CompletenessFinding(BaseModel):
    """A typed v3 knowledge state, independent from extraction validity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str = Field(min_length=1, max_length=180)
    field_path: str = Field(min_length=1, max_length=240)
    status: CompletenessStatus
    message: str = Field(min_length=1, max_length=1000)
    source_section_ids: list[str] = Field(default_factory=list, max_length=5000)
    conflict_id: str | None = Field(default=None, max_length=120)
    approval_blocking: bool = True

    @model_validator(mode="after")
    def validate_knowledge_state(self):
        if self.status is CompletenessStatus.KNOWN:
            if not self.source_section_ids or self.conflict_id is not None:
                raise ValueError("known completeness requires source evidence and no conflict")
        elif self.status is CompletenessStatus.CONFLICTED:
            if self.conflict_id is None:
                raise ValueError("conflicted completeness requires a conflict reference")
        elif self.conflict_id is not None:
            raise ValueError("unknown completeness cannot claim a conflict reference")
        if len(self.source_section_ids) != len(set(self.source_section_ids)):
            raise ValueError("completeness source IDs must be unique")
        return self


class CompletenessFindingClass(str, Enum):
    """Internal producer classification; it is intentionally not persisted."""

    GLOBAL_UNKNOWN = "global_unknown"
    UNMAPPED_WORKFLOW = "unmapped_workflow"
    SOURCE_CONFLICT = "source_conflict"
    COMPILATION_GAP = "compilation_gap"
    OTHER_EXISTING = "other_existing"


@dataclass(frozen=True)
class _ClassifiedCompletenessFinding:
    finding: CompletenessFinding
    finding_class: CompletenessFindingClass
    consolidation_key: str


class PlaybookAssemblyValidationError(ValueError):
    """Local final-playbook validation failed after extraction completed."""

    def __init__(self, diagnostics: dict[str, Any]):
        super().__init__("SalesPlaybook final assembly validation failed")
        self.diagnostics = diagnostics

    @classmethod
    def from_exception(
        cls,
        error: Exception,
        metrics: dict[str, Any],
    ) -> PlaybookAssemblyValidationError:
        return cls(_assembly_validation_diagnostics(error, metrics=metrics))


def _finding_key(finding_class: CompletenessFindingClass, *parts: Any) -> str:
    return json.dumps(
        [finding_class.value, *parts], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )


def _consolidate_completeness_findings(
    candidates: Sequence[_ClassifiedCompletenessFinding],
) -> tuple[list[CompletenessFinding], dict[str, Any]]:
    """Merge only findings with an identical producer-defined structural key."""
    consolidated: list[_ClassifiedCompletenessFinding] = []
    index_by_key: dict[str, int] = {}
    duplicate_group_keys: set[str] = set()
    for candidate in candidates:
        index = index_by_key.get(candidate.consolidation_key)
        if index is None:
            index_by_key[candidate.consolidation_key] = len(consolidated)
            consolidated.append(candidate)
            continue
        duplicate_group_keys.add(candidate.consolidation_key)
        previous = consolidated[index]
        first = previous.finding
        incoming = candidate.finding
        source_ids = list(dict.fromkeys((*first.source_section_ids, *incoming.source_section_ids)))
        consolidated[index] = _ClassifiedCompletenessFinding(
            finding=first.model_copy(update={"source_section_ids": source_ids}),
            finding_class=previous.finding_class,
            consolidation_key=previous.consolidation_key,
        )

    counts = {item.value: 0 for item in CompletenessFindingClass}
    for item in consolidated:
        counts[item.finding_class.value] += 1
    findings = [item.finding for item in consolidated]
    metrics = {
        "pre_consolidation_count": len(candidates),
        "post_consolidation_count": len(findings),
        "counts_by_finding_class": counts,
        "duplicate_groups_collapsed": len(duplicate_group_keys),
        "findings_with_multiple_provenance": sum(
            len(item.source_section_ids) > 1 for item in findings
        ),
        "global_unknown_count": counts[CompletenessFindingClass.GLOBAL_UNKNOWN.value],
        "unmapped_workflow_count": counts[CompletenessFindingClass.UNMAPPED_WORKFLOW.value],
        "source_conflict_count": counts[CompletenessFindingClass.SOURCE_CONFLICT.value],
        "compilation_gap_count": counts[CompletenessFindingClass.COMPILATION_GAP.value],
    }
    return findings, metrics


def _assembly_validation_diagnostics(
    error: Exception,
    *,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    field = "unknown"
    validation_code = "local_validation_error"
    actual_count = None
    maximum = None
    if isinstance(error, ValidationError):
        errors = error.errors(include_input=False)
        if errors:
            item = errors[0]
            raw_field = ".".join(str(part) for part in item.get("loc", ()))
            field = raw_field if re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,120}", raw_field) else "unknown"
            raw_type = item.get("type")
            validation_code = raw_type if isinstance(raw_type, str) and re.fullmatch(
                r"[a-z0-9_]{1,80}", raw_type,
            ) else "local_validation_error"
            context = item.get("ctx") or {}
            actual_count = context.get("actual_length") if type(context.get("actual_length")) is int else None
            maximum = context.get("max_length") if type(context.get("max_length")) is int else None
    return {
        "phase": "semantic_playbook_assembly",
        "contract": "SalesPlaybookContentV3",
        "validation_code": validation_code,
        "field": field,
        "actual_count": actual_count,
        "maximum": maximum,
        **metrics,
    }


class SparseOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = Field(default=None, max_length=5000)
    price_context: str | None = Field(default=None, max_length=1000)


class SparseStrategyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_segments: list[TargetSegment] = Field(default_factory=list, max_length=20)
    offer: SparseOffer = Field(default_factory=SparseOffer)
    positioning: list[str] = Field(default_factory=list, max_length=20)
    commercial_goal: str | None = Field(default=None, max_length=3000)
    qualification_principles: list[PolicyRule] = Field(default_factory=list, max_length=20)


class SparseProspectingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourcing_rules: list[PolicyRule] = Field(default_factory=list, max_length=20)
    classification_rules: list[ClassificationRule] = Field(default_factory=list, max_length=20)
    prioritization_rules: list[PolicyRule] = Field(default_factory=list, max_length=20)


class SparseSetterPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objectives: list[str] = Field(default_factory=list, max_length=20)
    allowed_actions: list[str] = Field(default_factory=list, max_length=20)
    forbidden_rules: list[PolicyRule] = Field(default_factory=list, max_length=30)
    language_rules: list[PolicyRule] = Field(default_factory=list, max_length=20)
    handoff_conditions: list[ConditionRule] = Field(default_factory=list, max_length=20)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)


class SparseCloserPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objectives: list[str] = Field(default_factory=list, max_length=20)
    discovery_questions: list[DiscoveryQuestion] = Field(default_factory=list, max_length=20)
    fit_signals: list[PolicyRule] = Field(default_factory=list, max_length=20)
    no_fit_signals: list[PolicyRule] = Field(default_factory=list, max_length=20)
    objection_rules: list[ObjectionRule] = Field(default_factory=list, max_length=20)
    closing_rules: list[PolicyRule] = Field(default_factory=list, max_length=20)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)


class SalesPlaybookContentV3(SalesPlaybookContent):
    """Sparse v3 DRAFT representation plus a separate semantic authority path."""

    contract_version: Literal[SEMANTIC_PLAYBOOK_CONTRACT_VERSION] = SEMANTIC_PLAYBOOK_CONTRACT_VERSION
    schema_version: Literal[SEMANTIC_PLAYBOOK_CONTRACT_VERSION] = SEMANTIC_PLAYBOOK_CONTRACT_VERSION
    strategy: SparseStrategyPolicy
    prospecting: SparseProspectingPolicy
    workflow: ReviewableWorkflowPolicy
    setter_policy: SparseSetterPolicy
    closer_policy: SparseCloserPolicy
    channel_policies: list[ChannelPolicy] = Field(default_factory=list, max_length=20)
    templates: list[PlaybookTemplate] = Field(default_factory=list, max_length=100)
    experiments: list[PlaybookExperiment] = Field(default_factory=list, max_length=20)
    pending_decisions: list[PendingDecision] = Field(default_factory=list, max_length=20)
    workflow_conflicts: list[WorkflowStageConflict] = Field(default_factory=list, max_length=13)
    workflow_missing_required_conditions: list[WorkflowConditionCode] = Field(default_factory=list, max_length=6)
    unmapped_workflow_stages: list[GenericUnmappedStage] = Field(default_factory=list, max_length=100)
    unmapped_workflow_transitions: list[GenericUnmappedTransition] = Field(default_factory=list, max_length=200)
    generic_source_claims: list[GenericSourceClaim] = Field(default_factory=list, max_length=500)
    completeness_findings: list[CompletenessFinding] = Field(default_factory=list, max_length=100)
    source_documents: list[SourceDocumentReference] = Field(default_factory=list, max_length=10)
    source_section_map: list[SourceSectionReference] = Field(default_factory=list, max_length=5000)
    source_conflicts: list[SourceConflictReview] = Field(default_factory=list, max_length=20)
    source_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    semantic_safety_version: Literal["6.3"] | None = None
    extraction_mode: Literal["historical_v3", "generic_v3"] | None = None
    semantic_policy: SemanticPlaybookPolicy
    generic_workflow_graph: GenericWorkflowGraphV1 | None = None

    @model_validator(mode="after")
    def validate_compiled_stage_rules(self):
        expected_missing_conditions = (
            REQUIRED_WORKFLOW_CONDITIONS - {transition.condition for transition in self.workflow.transitions}
            if self.extraction_mode != "generic_v3" else set()
        )
        if set(self.workflow_missing_required_conditions) != expected_missing_conditions:
            raise ValueError("missing required workflow conditions differ from extraction mode and structure")
        if len(self.workflow_missing_required_conditions) != len(set(self.workflow_missing_required_conditions)):
            raise ValueError("duplicate missing required workflow condition")
        stage_codes = {stage.code for stage in self.workflow.stages}
        unresolved_stage_conflicts = {
            conflict.stage_code for conflict in self.workflow_conflicts
            if conflict.conflict_type == "stage" and conflict.status == "needs_review"
        }
        unknown_stage_codes = {
            WorkflowStageCode(item.field_path.removeprefix("workflow.stages."))
            for item in self.completeness_findings
            if item.status is CompletenessStatus.UNKNOWN
            and item.field_path.startswith("workflow.stages.")
            and item.field_path.removeprefix("workflow.stages.") in {code.value for code in WorkflowStageCode}
        }
        expected_absent_stage_codes = set(WorkflowStageCode) - stage_codes
        if self.extraction_mode != "generic_v3" and (
            expected_absent_stage_codes != unresolved_stage_conflicts | unknown_stage_codes
        ):
            raise ValueError("missing canonical workflow stage must be conflicted or explicitly unknown")
        finding_ids = [item.finding_id for item in self.completeness_findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("completeness finding IDs must be unique")
        known_source_ids = set(self.semantic_policy.source_section_ids)
        if any(not set(item.source_section_ids) <= known_source_ids for item in self.completeness_findings):
            raise ValueError("completeness finding references an unknown source section")
        if any(not set(item.claim.source_section_ids) <= known_source_ids
               for item in (*self.unmapped_workflow_stages, *self.unmapped_workflow_transitions)):
            raise ValueError("unmapped workflow claim references an unknown source section")
        if any(not set(item.source_section_ids) <= known_source_ids for item in self.generic_source_claims):
            raise ValueError("generic source claim references an unknown source section")
        if self.generic_workflow_graph is not None:
            assertion_ids = {item.assertion_id for item in self.semantic_policy.assertions}
            if any(not set(item.assertion_ids) <= assertion_ids for item in self.generic_workflow_graph.nodes):
                raise ValueError("generic workflow node references an unknown semantic assertion")
            if any(not set(item.assertion_ids) <= assertion_ids for item in self.generic_workflow_graph.relations):
                raise ValueError("generic workflow relation references an unknown semantic assertion")
            if any(not set(item.source_section_ids) <= known_source_ids for item in self.generic_workflow_graph.nodes):
                raise ValueError("generic workflow node references unknown source provenance")
            if any(not set(item.source_section_ids) <= known_source_ids for item in self.generic_workflow_graph.relations):
                raise ValueError("generic workflow relation references unknown source provenance")
            if any(not set(item.assertion_ids) <= assertion_ids for item in self.generic_workflow_graph.conflicts):
                raise ValueError("generic workflow conflict references an unknown semantic assertion")
            if any(not set(item.source_section_ids) <= known_source_ids for item in self.generic_workflow_graph.conflicts):
                raise ValueError("generic workflow conflict references unknown source provenance")
            if any(not set(item.source_section_ids) <= known_source_ids for item in self.generic_workflow_graph.findings):
                raise ValueError("generic workflow finding references unknown source provenance")
            if self.extraction_mode != "generic_v3":
                raise ValueError("generic workflow graph is only valid for new generic extraction")
        for conflict in self.workflow_conflicts:
            candidate_ids = {candidate.candidate_id for candidate in conflict.candidates}
            if conflict.status == "needs_review":
                if conflict.resolved_candidate_id is not None:
                    raise ValueError("unresolved workflow conflict cannot select a candidate")
                if conflict.conflict_type == "stage" and conflict.stage_code in stage_codes:
                    raise ValueError("unresolved workflow stage conflict cannot have canonical authority")
                if conflict.conflict_type == "transition" and conflict.stage_code not in stage_codes:
                    raise ValueError("transition conflict must retain its unconflicted canonical stage")
            else:
                if conflict.resolved_candidate_id not in candidate_ids:
                    raise ValueError("resolved workflow conflict must identify a retained candidate")
                candidate = next(item for item in conflict.candidates
                                 if item.candidate_id == conflict.resolved_candidate_id)
                if conflict.conflict_type == "stage":
                    if conflict.stage_code not in stage_codes:
                        raise ValueError("resolved workflow stage conflict lacks its canonical stage")
                    expected_stage = workflow_stage_from_candidate(candidate)
                    actual_stage = next(stage for stage in self.workflow.stages
                                        if stage.code is conflict.stage_code)
                    if expected_stage != actual_stage:
                        raise ValueError("canonical stage differs from the explicitly selected candidate")
                expected_transitions = workflow_transitions_from_candidate(candidate)
                if any(item not in self.workflow.transitions for item in expected_transitions):
                    raise ValueError("resolved workflow transitions differ from the selected candidate")
        expected_conflicted_stages = sorted(
            unresolved_stage_conflicts, key=lambda code: list(WorkflowStageCode).index(code)
        )
        if self.semantic_policy.workflow_conflicted_stage_codes != expected_conflicted_stages:
            raise ValueError("semantic compilation conflict stages differ from workflow review state")
        if self.extraction_mode == "generic_v3":
            expected_unmapped_policy_stages = {
                code for code in self.semantic_policy.workflow_unmapped_stage_codes
                if code not in stage_codes and code not in unresolved_stage_conflicts
            }
            if expected_unmapped_policy_stages != set(self.semantic_policy.workflow_unmapped_stage_codes):
                raise ValueError("generic policy placement references a mapped or conflicted workflow stage")
        traces = {
            tuple(trace.source_assertion_ids): trace
            for trace in self.semantic_policy.compilation.compiled_rules
        }
        if len(traces) != len(self.semantic_policy.compilation.compiled_rules):
            raise ValueError("duplicate compiled trace")
        placed = set()
        expected = {}
        for placement in self.semantic_policy.placements:
            key = tuple(placement.source_assertion_ids)
            if key not in traces or key in placed:
                raise ValueError("placement has no unique compiled trace")
            placed.add(key)
            expected.setdefault(placement.stage_code, []).append(traces[key].rule)
        if placed != set(traces):
            raise ValueError("compiled rule lacks stage placement")
        for stage in self.workflow.stages:
            if stage.rules != expected.get(stage.code, []):
                raise ValueError("workflow rules differ from placed semantic compilation")
        return self




def assemble_semantic_playbook(
    extraction: ManualSemanticExtractionResult,
    *,
    strategy: StrategyPolicy,
    prospecting: ProspectingPolicy,
    setter_policy: SetterPolicy,
    closer_policy: CloserPolicy,
    channel_policies: list[ChannelPolicy],
    templates: list[PlaybookTemplate],
    experiments: list[PlaybookExperiment],
    pending_decisions: list[PendingDecision],
    governance_evidence: Sequence[MaturityEvidence] = (),
    on_assembly_metrics: Callable[[dict[str, Any]], None] | None = None,
) -> SalesPlaybookContentV3:
    """Compile only typed, provenance checked assertions from the Task 2 seam.

    This does not establish natural-language entailment. All candidate rules
    require explicit source review before they enter an approved projection.
    """
    strategy_v3 = SparseStrategyPolicy.model_validate(strategy.model_dump(mode="python"))
    prospecting_v3 = SparseProspectingPolicy.model_validate(prospecting.model_dump(mode="python"))
    setter_policy_v3 = SparseSetterPolicy.model_validate(setter_policy.model_dump(mode="python"))
    closer_policy_v3 = SparseCloserPolicy.model_validate(closer_policy.model_dump(mode="python"))
    workflow = (
        _expand_sparse_generic_workflow(extraction.structural_workflow)
        if extraction.generic else
        expand_workflow(extraction.structural_workflow, allow_reviewable=True)
    )
    if any(stage.rules for stage in workflow.stages):
        raise ValueError("semantic assembly cannot accept legacy workflow rules")
    known_sections = set(extraction.source_section_ids)
    if any(not set(item.source_section_ids) <= known_sections for item in governance_evidence):
        raise ValueError("governance provenance references unknown source section")
    assertions = [item for owner in extraction.owner_results for item in owner.assertions]
    if any(not set(item.source_section_ids) <= known_sections for item in assertions):
        raise ValueError("semantic assertion has foreign source provenance")
    resolved = resolve_semantic_maturity(assertions, governance_evidence)
    resolved_by_id = {item.assertion_id: item for item in resolved}
    compiled = SemanticCompilation(input_assertion_ids=[item.assertion_id for item in resolved])
    placements = []
    findings = []
    unresolved_workflow_stages = {
        conflict.stage_code for conflict in extraction.structural_workflow.conflicts
        if conflict.conflict_type == "stage" and conflict.status == "needs_review"
    }
    workflow_stage_codes = {stage.code for stage in workflow.stages}
    unmapped_policy_stage_codes = {
        code for code in (_PLACEMENT_SCOPE.get(assertion.scope_text.casefold().strip())
                          for assertion in resolved if assertion.scope_text)
        if code is not None and code not in workflow_stage_codes and code not in unresolved_workflow_stages
    }
    for owner in extraction.owner_results:
        owner_assertions = [resolved_by_id[item.assertion_id] for item in owner.assertions]
        owner_compilation = compile_semantic_policies(owner_assertions)
        compiled.unresolved.extend(owner_compilation.unresolved)
        if owner.conflicts:
            findings.append(f"normalized modality conflict in {owner.workflow_owner}")
        for trace in owner_compilation.compiled_rules:
            scopes = {resolved_by_id[item].scope_text.casefold().strip() for item in trace.source_assertion_ids}
            stage_code = _PLACEMENT_SCOPE.get(next(iter(scopes))) if len(scopes) == 1 else None
            if (
                (not extraction.generic and owner.workflow_owner != "touch_1")
                or stage_code is not WorkflowStageCode.TOUCH_1
            ):
                contributors = [resolved_by_id[item] for item in trace.source_assertion_ids]
                compiled.unresolved.append(UnresolvedSemanticPolicy(
                    assertions=contributors, status=CompilationStatus.AMBIGUOUS,
                    reason="executable stage placement is unresolved",
                    source_section_ids=list(dict.fromkeys(
                        section for item in contributors for section in item.source_section_ids
                    )),
                ))
                continue
            if stage_code in unresolved_workflow_stages or not any(
                stage.code is stage_code for stage in workflow.stages
            ):
                contributors = [resolved_by_id[item] for item in trace.source_assertion_ids]
                compiled.unresolved.append(UnresolvedSemanticPolicy(
                    assertions=contributors, status=CompilationStatus.AMBIGUOUS,
                    reason=("workflow stage conflict requires human resolution"
                            if stage_code in unresolved_workflow_stages else
                            "source-backed policy references a stage not mapped in the workflow"),
                    source_section_ids=list(dict.fromkeys(
                        section for item in contributors for section in item.source_section_ids
                    )),
                ))
                continue
            compiled.compiled_rules.append(trace)
            placements.append(CompiledRulePlacement(
                stage_code=stage_code, source_assertion_ids=trace.source_assertion_ids,
            ))
            stage = next(item for item in workflow.stages if item.code is stage_code)
            stage.rules.append(trace.rule)
    if any(item.status is CompilationStatus.CONFLICT for item in compiled.unresolved):
        findings.append("normalized semantic policy conflict")
    completeness_candidates: list[_ClassifiedCompletenessFinding] = []
    if extraction.generic:
        field_source_topics = {
            "strategy.offer.summary": {"offer"},
            "strategy.offer.price_context": {"price"},
            "strategy.target_segments": {"target_customer"},
            "prospecting.classification_rules": {"classification"},
            "channel_policies": {"channel"},
        }
        required_sparse_fields = {
            "strategy.offer.summary": strategy_v3.offer.summary,
            "strategy.offer.price_context": strategy_v3.offer.price_context,
            "strategy.target_segments": strategy_v3.target_segments or None,
            "strategy.commercial_goal": strategy_v3.commercial_goal,
            "prospecting.classification_rules": prospecting_v3.classification_rules or None,
            "setter_policy.forbidden_rules": setter_policy_v3.forbidden_rules or None,
            "closer_policy.fit_signals": closer_policy_v3.fit_signals or None,
            "channel_policies": channel_policies or None,
        }
        for field_path, value in required_sparse_fields.items():
            if value is None:
                relevant_claims = [
                    claim for claim in extraction.generic_source_claims
                    if claim.topic in field_source_topics.get(field_path, set())
                ]
                finding_class = (
                    CompletenessFindingClass.COMPILATION_GAP if relevant_claims
                    else CompletenessFindingClass.GLOBAL_UNKNOWN
                )
                evidence = list(dict.fromkeys(
                    section_id for claim in relevant_claims for section_id in claim.source_section_ids
                ))
                message = (
                    f"Source-backed information for {field_path} was preserved as claims but is not "
                    "compiled into the structured policy; human review is required."
                    if relevant_claims else
                    f"No source-backed value was captured for {field_path}; human review is required."
                )
                finding = CompletenessFinding(
                    finding_id=f"unknown:{field_path}", field_path=field_path,
                    status=CompletenessStatus.UNKNOWN,
                    message=message,
                    source_section_ids=evidence,
                )
                completeness_candidates.append(_ClassifiedCompletenessFinding(
                    finding=finding,
                    finding_class=finding_class,
                    consolidation_key=_finding_key(
                        finding_class, field_path,
                        CompletenessStatus.UNKNOWN.value, True,
                    ),
                ))
        for field_path, value in {
            "templates": templates or None,
            "experiments": experiments or None,
            "pending_decisions": pending_decisions or None,
        }.items():
            if value is None:
                finding = CompletenessFinding(
                    finding_id=f"unknown:{field_path}", field_path=field_path,
                    status=CompletenessStatus.UNKNOWN,
                    message=f"No source-backed content was captured for {field_path}.",
                    approval_blocking=False,
                )
                completeness_candidates.append(_ClassifiedCompletenessFinding(
                    finding=finding,
                    finding_class=CompletenessFindingClass.OTHER_EXISTING,
                    consolidation_key=_finding_key(
                        CompletenessFindingClass.OTHER_EXISTING, field_path,
                        CompletenessStatus.UNKNOWN.value, False,
                    ),
                ))
        claim_conflicts = {}
        for claim in extraction.generic_source_claims:
            if claim.conflict_group_id:
                claim_conflicts.setdefault(claim.conflict_group_id, []).append(claim)
        for conflict_id, claims in claim_conflicts.items():
            if len(claims) < 2:
                raise PlaybookAssemblyValidationError({
                    "phase": "semantic_playbook_assembly",
                    "contract": "generic_source_conflict",
                    "validation_code": "conflict_group_requires_multiple_claims",
                    "field": "generic_source_claims.conflict_group_id",
                    "actual_count": len(claims),
                    "maximum": None,
                })
            finding = CompletenessFinding(
                finding_id=f"conflicted-source-claims:{conflict_id}",
                field_path=f"source_claims.{claims[0].topic}",
                status=CompletenessStatus.CONFLICTED,
                message="The source contains incompatible statements that need a human decision.",
                source_section_ids=list(dict.fromkeys(
                    section_id for claim in claims for section_id in claim.source_section_ids
                )),
                conflict_id=conflict_id,
            )
            completeness_candidates.append(_ClassifiedCompletenessFinding(
                finding=finding,
                finding_class=CompletenessFindingClass.SOURCE_CONFLICT,
                consolidation_key=_finding_key(
                    CompletenessFindingClass.SOURCE_CONFLICT, conflict_id, finding.field_path,
                ),
            ))
        generic_graph = extraction.generic_workflow_graph
        if generic_graph is not None:
            relation_by_id = {item.relation_id: item for item in generic_graph.relations}
            for conflict in generic_graph.conflicts:
                finding = CompletenessFinding(
                    finding_id=f"generic-workflow-conflict:{conflict.conflict_id}",
                    field_path=f"workflow.graph.conflicts.{conflict.conflict_id}",
                    status=CompletenessStatus.CONFLICTED,
                    message="The source supports competing workflow routes; a human decision is required.",
                    source_section_ids=conflict.source_section_ids,
                    conflict_id=conflict.conflict_id,
                )
                completeness_candidates.append(_ClassifiedCompletenessFinding(
                    finding=finding,
                    finding_class=CompletenessFindingClass.SOURCE_CONFLICT,
                    consolidation_key=_finding_key(
                        CompletenessFindingClass.SOURCE_CONFLICT, "generic-workflow", conflict.conflict_id,
                    ),
                ))
            for graph_finding in generic_graph.findings:
                related_relations = [relation_by_id[item] for item in graph_finding.relation_ids
                                     if item in relation_by_id]
                conflict_ids = {item.conflict_id for item in related_relations if item.conflict_id}
                if graph_finding.kind == "source_conflict" and conflict_ids:
                    continue
                finding_class = (
                    CompletenessFindingClass.SOURCE_CONFLICT
                    if graph_finding.kind == "source_conflict"
                    else CompletenessFindingClass.COMPILATION_GAP
                    if graph_finding.kind in {
                        "unsupported_relation", "contradicted_relation", "insufficient_relation_evidence",
                        "invalid_timing", "missing_guard", "compilation_gap",
                    }
                    else CompletenessFindingClass.UNMAPPED_WORKFLOW
                )
                conflict_id = (
                    next(iter(conflict_ids), graph_finding.finding_id)
                    if finding_class is CompletenessFindingClass.SOURCE_CONFLICT else None
                )
                finding = CompletenessFinding(
                    finding_id=f"generic-workflow:{graph_finding.finding_id}",
                    field_path=f"workflow.graph.{graph_finding.kind}",
                    status=(CompletenessStatus.CONFLICTED if finding_class is CompletenessFindingClass.SOURCE_CONFLICT
                            else CompletenessStatus.UNKNOWN),
                    message=graph_finding.message,
                    source_section_ids=graph_finding.source_section_ids,
                    conflict_id=conflict_id,
                )
                completeness_candidates.append(_ClassifiedCompletenessFinding(
                    finding=finding,
                    finding_class=finding_class,
                    consolidation_key=_finding_key(
                        finding_class, graph_finding.kind, graph_finding.relation_ids,
                        graph_finding.node_ids, conflict_id,
                    ),
                ))

        generic_workflow = extraction.generic_workflow
        unmapped_stages = generic_workflow.unmapped_stages if generic_workflow else ()
        unmapped_transitions = generic_workflow.unmapped_transitions if generic_workflow else ()
        has_workflow_evidence = bool(
            extraction.structural_workflow.stages
            or extraction.structural_workflow.transitions
            or extraction.structural_workflow.conflicts
            or unmapped_stages or unmapped_transitions
            or (generic_graph is not None and (generic_graph.nodes or generic_graph.relations))
        )
        if not has_workflow_evidence:
            workflow_claims = [
                claim for claim in extraction.generic_source_claims if claim.topic == "workflow"
            ]
            finding_class = (
                CompletenessFindingClass.COMPILATION_GAP if workflow_claims
                else CompletenessFindingClass.GLOBAL_UNKNOWN
            )
            finding = CompletenessFinding(
                finding_id="unknown:workflow", field_path="workflow",
                status=CompletenessStatus.UNKNOWN,
                message=(
                    "Workflow-related source claims were preserved but not represented as structured "
                    "stages or transitions; human review is required."
                    if workflow_claims else
                    "No source-backed workflow information was captured; human review is required."
                ),
                source_section_ids=list(dict.fromkeys(
                    section_id for claim in workflow_claims for section_id in claim.source_section_ids
                )),
            )
            completeness_candidates.append(_ClassifiedCompletenessFinding(
                finding=finding,
                finding_class=finding_class,
                consolidation_key=_finding_key(
                    finding_class, "workflow",
                    CompletenessStatus.UNKNOWN.value, True,
                ),
            ))
        has_transition_evidence = bool(
            extraction.structural_workflow.transitions or unmapped_transitions
            or (generic_graph is not None and generic_graph.relations)
        )
        if has_workflow_evidence and not has_transition_evidence:
            workflow_claims = [
                claim for claim in extraction.generic_source_claims if claim.topic == "workflow"
            ]
            finding_class = (
                CompletenessFindingClass.COMPILATION_GAP if workflow_claims
                else CompletenessFindingClass.GLOBAL_UNKNOWN
            )
            finding = CompletenessFinding(
                finding_id="unknown:workflow.transitions",
                field_path="workflow.transitions",
                status=CompletenessStatus.UNKNOWN,
                message=(
                    "Workflow-related source claims were preserved but no structured transition was "
                    "compiled; human review is required."
                    if workflow_claims else
                    "No source-backed workflow transitions were captured; human review is required."
                ),
                source_section_ids=list(dict.fromkeys(
                    section_id for claim in workflow_claims for section_id in claim.source_section_ids
                )),
            )
            completeness_candidates.append(_ClassifiedCompletenessFinding(
                finding=finding,
                finding_class=finding_class,
                consolidation_key=_finding_key(
                    finding_class, "workflow.transitions",
                    CompletenessStatus.UNKNOWN.value, True,
                ),
            ))
        for claim in unmapped_stages:
            finding = CompletenessFinding(
                finding_id=f"unmapped-stage:{claim.claim.source_stage_id}",
                field_path=f"workflow.source_stages.{claim.claim.source_stage_id}",
                status=CompletenessStatus.UNKNOWN,
                message="A source workflow stage is preserved for human mapping.",
                source_section_ids=claim.claim.source_section_ids,
            )
            semantic_claim = claim.claim.model_dump(
                mode="json", exclude={"source_stage_id", "source_section_ids"},
            )
            completeness_candidates.append(_ClassifiedCompletenessFinding(
                finding=finding,
                finding_class=CompletenessFindingClass.UNMAPPED_WORKFLOW,
                consolidation_key=_finding_key(
                    CompletenessFindingClass.UNMAPPED_WORKFLOW, "stage", claim.reason, semantic_claim,
                ),
            ))
        for index, claim in enumerate(unmapped_transitions, start=1):
            transition_conflict = claim.reason.startswith("Conflicting source-backed destinations")
            conflict_id = (
                f"workflow-transition:{claim.claim.from_stage_name}:{claim.claim.condition_text}"
                if transition_conflict else None
            )
            finding_class = (
                CompletenessFindingClass.SOURCE_CONFLICT if transition_conflict
                else CompletenessFindingClass.UNMAPPED_WORKFLOW
            )
            finding = CompletenessFinding(
                finding_id=f"unmapped-transition:{index}",
                field_path="workflow.source_transitions",
                status=CompletenessStatus.CONFLICTED if transition_conflict else CompletenessStatus.UNKNOWN,
                message="A source workflow relationship is preserved for human review.",
                source_section_ids=claim.claim.source_section_ids,
                conflict_id=conflict_id,
            )
            semantic_claim = claim.claim.model_dump(mode="json", exclude={"source_section_ids"})
            completeness_candidates.append(_ClassifiedCompletenessFinding(
                finding=finding,
                finding_class=finding_class,
                consolidation_key=_finding_key(
                    finding_class, "transition", claim.reason, semantic_claim, conflict_id,
                ),
            ))
    completeness, assembly_metrics = _consolidate_completeness_findings(completeness_candidates)
    if on_assembly_metrics is not None:
        on_assembly_metrics(dict(assembly_metrics))
    if len(completeness) > 100:
        raise PlaybookAssemblyValidationError({
            "phase": "semantic_playbook_assembly",
            "contract": "SalesPlaybookContentV3",
            "validation_code": "completeness_findings_too_many",
            "field": "completeness_findings",
            "actual_count": len(completeness),
            "maximum": 100,
            **assembly_metrics,
        })
    try:
        return SalesPlaybookContentV3(
            strategy=strategy_v3,
            prospecting=prospecting_v3,
            workflow=workflow,
            setter_policy=setter_policy_v3,
            closer_policy=closer_policy_v3,
            channel_policies=channel_policies,
            templates=templates,
            experiments=experiments,
            pending_decisions=pending_decisions,
            workflow_conflicts=extraction.structural_workflow.conflicts,
            workflow_missing_required_conditions=extraction.structural_workflow.missing_required_conditions,
            unmapped_workflow_stages=(list(extraction.generic_workflow.unmapped_stages)
                                      if extraction.generic_workflow else []),
            unmapped_workflow_transitions=(list(extraction.generic_workflow.unmapped_transitions)
                                           if extraction.generic_workflow else []),
            generic_source_claims=list(extraction.generic_source_claims),
            completeness_findings=completeness,
            extraction_mode="generic_v3" if extraction.generic else "historical_v3",
            semantic_policy=SemanticPlaybookPolicy(
                assertions=resolved, compilation=compiled, placements=placements,
                source_section_ids=list(extraction.source_section_ids),
                source_review_required=extraction.source_review_required,
                extraction_complete=bool(extraction.chunk_observations) and all(
                    item.accepted for item in extraction.chunk_observations
                ),
                extraction_truncated=any(
                    item.finish_reason == "length" for item in extraction.chunk_observations
                ),
                safety_findings=findings,
                workflow_conflicted_stage_codes=sorted(
                    unresolved_workflow_stages, key=lambda code: list(WorkflowStageCode).index(code)
                ),
                workflow_unmapped_stage_codes=sorted(
                    unmapped_policy_stage_codes, key=lambda code: list(WorkflowStageCode).index(code),
                ),
            ),
            generic_workflow_graph=extraction.generic_workflow_graph,
        )
    except (ValidationError, ValueError, TypeError) as error:
        diagnostics = _assembly_validation_diagnostics(error, metrics=assembly_metrics)
        if isinstance(error, PlaybookAssemblyValidationError):
            raise
        raise PlaybookAssemblyValidationError(diagnostics) from error


def _expand_sparse_generic_workflow(extraction) -> ReviewableWorkflowPolicy:
    """Expand only explicitly mapped generic claims; omitted stages stay absent."""
    stages = [WorkflowStage(
        code=item.stage_code, step_number=index, name=item.name, objective=item.objective,
        actions=item.actions, entry_conditions=item.entry_conditions,
        exit_conditions=item.exit_conditions, timing=item.timing,
        responsible_role=item.responsible_role, rules=[], maturity=item.maturity,
        provenance=PlaybookProvenance(
            origin=PlaybookOrigin.MANUAL, source_ref=",".join(item.source_section_ids),
        ),
    ) for index, item in enumerate(extraction.stages, start=1)]
    valid = {item.code for item in stages}
    transitions = []
    for item in extraction.transitions:
        if item.from_stage not in valid or item.to_stage not in valid:
            raise ValueError("generic workflow transition references an unmapped stage")
        transitions.append(WorkflowTransition(
            from_stage=item.from_stage, to_stage=item.to_stage, condition=item.condition,
            outcome=item.outcome, wait_hours=item.wait_hours, description=item.description,
            maturity=item.maturity,
            provenance=PlaybookProvenance(
                origin=PlaybookOrigin.MANUAL, source_ref=",".join(item.source_section_ids),
            ),
        ))
    return ReviewableWorkflowPolicy(stages=stages, transitions=transitions)
