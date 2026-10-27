from __future__ import annotations

import json
import logging
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.identity.models import Workspace
from app.modules.sales_playbook.document_models import SourceDocument
from app.modules.sales_playbook.extraction import ExtractionKind
from app.modules.sales_playbook.generator import (
    ContractReadyDraftGenerator,
    SalesPlaybookGenerator,
)
from app.modules.sales_playbook.models import (
    SalesPlaybook,
    SalesPlaybookSourceType,
    SalesPlaybookStatus,
)
from app.modules.sales_playbook.schemas import (
    ApprovalReadiness,
    ApprovalReadinessBlocker,
    ChannelPolicy,
    MissingRequiredTransition,
    PlaybookMaturity,
    PlaybookOrigin,
    PlaybookRuleCode,
    PolicyRuleEffect,
    SalesPlaybookContent,
    SalesPlaybookDraftCreate,
    SalesPlaybookWorkflowTransitionComplete,
    WorkflowConditionCode,
    WorkflowOutcome,
    WorkflowStageCode,
)
from app.modules.sales_playbook.semantic_playbook import (
    CompletenessStatus,
    PlaybookAssemblyValidationError,
    SalesPlaybookContentV3,
    SemanticWorkflowReview,
    SourceDocumentReference,
    complete_required_workflow_transition,
    resolve_semantic_workflow_conflict,
    validate_approved_workflow_structure,
)
from app.modules.sales_playbook.semantic_policy import (
    CompilationStatus,
    SemanticConcept,
    SemanticModality,
)
from app.modules.sales_playbook.source_consistency import detect_source_conflicts
from app.modules.sales_playbook.stage_aware import WorkflowUnitKind
from app.modules.sales_playbook.workflow_safety import validate_workflow_semantics
from app.modules.seller_profile.models import SellerProfile

logger = logging.getLogger(__name__)
_SAFE_FINISH_REASONS = frozenset({"stop", "length", "content_filter"})
_SAFE_EXTRACTION_KINDS = frozenset(
    {kind.value for kind in ExtractionKind}
    | {f"workflow_unit.{kind.value}" for kind in WorkflowUnitKind}
)
MAX_AGGREGATE_NORMALIZED_SOURCE_BYTES = 2 * 1024 * 1024


class IdempotencyConflict(ValueError):
    """A client key was already used with different creation input."""


class SemanticGenerationUnavailable(RuntimeError):
    """Manual and document drafts require a configured semantic generator."""


class SalesPlaybookGenerationError(RuntimeError):
    """Draft generation failed before a playbook could be stored."""


class LocalSalesPlaybookProcessingError(SalesPlaybookGenerationError):
    """A deterministic local phase failed after provider output was accepted."""


@dataclass(frozen=True)
class _PreparedGenerationContext:
    """Validated, service-prepared inputs for one SalesPlaybook generation call."""

    profile: SellerProfile | None
    semantic_source: bool
    source_conflicts: tuple[Any, ...]
    source_input_sha256: str | None
    source_section_map: tuple[Any, ...]
    authorized_extraction_sections: tuple[Any, ...]
    semantic_generator: Callable[..., Any] | None
    trusted_invocation: object | None


def _safe_failure_diagnostics(generator: Any) -> dict[str, Any]:
    """Keep only bounded structural metadata; never log provider text or metadata strings."""
    try:
        return _collect_safe_failure_diagnostics(generator)
    except Exception:
        # Diagnostics must never replace the original failure or expose provider data.
        return {"audit": None, "extractions": [], "semantic_observations": [], "assembly_metrics": None}


def _safe_phase(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    if re.fullmatch(r"[a-z_]+(?::[a-z0-9_]+)?", value) and len(value) <= 80:
        return value
    return "unknown"


def _safe_exception_message(exc: Exception, phase: str) -> str:
    # Exception text may contain source text or provider-controlled content.
    if isinstance(exc, ValueError) and phase == "workflow_assembly":
        return "Deterministic workflow assembly rejected extracted structure"
    if isinstance(exc, PlaybookAssemblyValidationError):
        return "Final playbook assembly validation failed"
    if isinstance(exc, ValidationError):
        return "Local schema validation failed"
    return "Generation failed; inspect sanitized stack frames"


def _collect_safe_failure_diagnostics(generator: Any) -> dict[str, Any]:
    audit = getattr(generator, "generation_audit", None)
    observations = getattr(generator, "last_semantic_observations", ())
    safe_observations = []
    for item in observations[:32] if isinstance(observations, tuple | list) else ():
        chunk_id = getattr(item, "chunk_id", None)
        finish_reason = getattr(item, "finish_reason", None)
        rejection_reason = getattr(item, "rejection_reason", None)
        safe_observations.append({
            "chunk_id": chunk_id if isinstance(chunk_id, str) and re.fullmatch(r"[a-z0-9_]{1,80}", chunk_id) else None,
            "http_status": _safe_number(getattr(item, "http_status", None)),
            "finish_reason": _safe_finish_reason(finish_reason),
            "input_tokens": _safe_number(getattr(item, "input_tokens", None)),
            "output_tokens": _safe_number(getattr(item, "output_tokens", None)),
            "total_tokens": _safe_number(getattr(item, "total_tokens", None)),
            "latency_ms": _safe_number(getattr(item, "latency_ms", None)),
            "content_chars": _safe_number(getattr(item, "content_chars", None)),
            "accepted": getattr(item, "accepted", None) is True,
            "rejection_kind": next((kind for kind in (
                "finish_reason", "provider response", "normalization", "structural validation",
                "provenance", "missing provider metadata",
            ) if isinstance(rejection_reason, str) and rejection_reason.startswith(kind)), None),
        })
    safe_extractions = []
    extractions = getattr(audit, "extractions", ())
    for item in extractions[:32] if isinstance(extractions, tuple | list) else ():
        kind = getattr(item, "kind", None)
        success = getattr(item, "success", None)
        json_parsed = getattr(item, "json_parsed", None)
        pydantic_valid = getattr(item, "pydantic_valid", None)
        safe_extractions.append({
            "kind": kind if isinstance(kind, str) and (
                kind in _SAFE_EXTRACTION_KINDS
                or re.fullmatch(r"generic_workflow\.generic_unit_[0-9]{3}", kind)
                or re.fullmatch(r"generic_workflow\.(?:nodes|window_[0-9]{3}|conflicts)", kind)
                or re.fullmatch(r"generic_source_claims\.generic_unit_[0-9]{3}", kind)
            ) else None,
            "finish_reason": _safe_finish_reason(getattr(item, "finish_reason", None)),
            "outcome": "accepted" if success is True else "rejected" if success is False else None,
            "json_parsed": json_parsed if type(json_parsed) is bool else None,
            "pydantic_valid": pydantic_valid if type(pydantic_valid) is bool else None,
            "input_tokens": _safe_number(getattr(item, "input_tokens", None)),
            "output_tokens": _safe_number(getattr(item, "output_tokens", None)),
            "total_tokens": _safe_number(getattr(item, "total_tokens", None)),
            "latency_ms": _safe_number(getattr(item, "latency_ms", None)),
        })
    return {
        "audit": None if audit is None else {
            "success": getattr(audit, "success", None) is True,
            "latency_ms": _safe_number(getattr(audit, "latency_ms", None)),
            "tokens": _safe_number(getattr(audit, "tokens", None)),
        },
        "extractions": safe_extractions,
        "semantic_observations": safe_observations,
        "assembly_metrics": _safe_assembly_metrics(getattr(generator, "last_assembly_metrics", None)),
    }


def _safe_assembly_metrics(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    numeric_fields = (
        "pre_consolidation_count", "post_consolidation_count", "duplicate_groups_collapsed",
        "findings_with_multiple_provenance", "global_unknown_count", "unmapped_workflow_count",
        "source_conflict_count", "compilation_gap_count",
    )
    allowed_classes = {"global_unknown", "unmapped_workflow", "source_conflict", "compilation_gap", "other_existing"}
    result = {
        field: number
        for field in numeric_fields
        if (number := value.get(field)) is not None and type(number) is int and 0 <= number <= 100_000
    }
    counts = value.get("counts_by_finding_class")
    if isinstance(counts, dict):
        result["counts_by_finding_class"] = {
            key: count for key, count in counts.items()
            if key in allowed_classes and type(count) is int and 0 <= count <= 100_000
        }
    return result or None


def _safe_finish_reason(value: Any) -> str | None:
    return value if isinstance(value, str) and value in _SAFE_FINISH_REASONS else None


def _safe_number(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def _safe_audit(audit: Any) -> dict[str, Any] | None:
    if audit is None:
        return None
    fields = (
        "provider", "model", "operation", "prompt_version", "latency_ms",
        "success", "tokens", "validation_attempts", "repaired",
    )
    result = {name: getattr(audit, name, None) for name in fields}
    result["extractions"] = [
        {name: getattr(item, name, None) for name in (
            "kind", "operation", "section_ids", "latency_ms", "total_tokens",
            "repair_count", "success", "json_parsed", "pydantic_valid", "finish_reason",
        )}
        for item in getattr(audit, "extractions", ())
    ]
    return result


def _fingerprint(source_type: SalesPlaybookSourceType, request_input: dict[str, Any],
                 seller_profile_id: UUID | None) -> str:
    canonical = json.dumps({
        "source_type": source_type.value,
        "request_input": request_input,
        "seller_profile_id": str(seller_profile_id) if seller_profile_id else None,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_digest(content: dict[str, Any]) -> str:
    """Identity of the exact draft content presented for human review."""
    return sha256(_canonical(content)).hexdigest()


def _approved_digest(content: dict[str, Any], reviewed_ids: list[str]) -> str:
    return sha256(_canonical({"content": content, "reviewed_guardrail_assertion_ids": sorted(reviewed_ids)})).hexdigest()


def approval_readiness(
    content: dict[str, Any] | SalesPlaybookContentV3, *, semantic_safety: bool = True,
) -> ApprovalReadiness:
    """Fail closed on unsafe v3 content; preserve unresolved noncritical claims for review."""
    try:
        parsed = SalesPlaybookContentV3.model_validate(content)
    except (ValidationError, ValueError, TypeError):
        return ApprovalReadiness(
            blockers=["Invalid semantic playbook content"],
            blocker_details=[ApprovalReadinessBlocker(kind="safety", identifier="invalid_content")],
            ready_for_approval=False,
        )
    policy = parsed.semantic_policy
    blockers = []
    blocker_details = []
    if parsed.generic_workflow_graph is not None:
        blockers.append("The generic workflow graph is not supported for Setter execution yet")
        blocker_details.append(ApprovalReadinessBlocker(
            kind="safety",
            identifier=parsed.generic_workflow_graph.contract_version,
            message="A generic Setter action-selection contract is required before approval.",
        ))
    if not policy.extraction_complete:
        blockers.append("Semantic extraction is incomplete")
        blocker_details.append(ApprovalReadinessBlocker(kind="extraction", identifier="incomplete"))
    if policy.extraction_truncated:
        blockers.append("Semantic extraction was truncated")
        blocker_details.append(ApprovalReadinessBlocker(kind="extraction", identifier="truncated"))
    if policy.safety_findings:
        blockers.append("Unresolved safety findings")
        blocker_details.extend(
            ApprovalReadinessBlocker(kind="safety", identifier=item)
            for item in policy.safety_findings
        )
    semantic_findings = []
    if semantic_safety:
        all_semantic_findings = validate_workflow_semantics(parsed.workflow.transitions, policy.assertions)
        reviewed_finding_ids = {item.finding_id for item in policy.workflow_reviews}
        known_finding_ids = {item.identifier for item in all_semantic_findings}
        stale_reviews = reviewed_finding_ids - known_finding_ids
        if stale_reviews:
            blockers.append("A human semantic review no longer matches the current workflow")
            blocker_details.extend(ApprovalReadinessBlocker(
                kind="semantic_workflow", identifier=f"stale_human_review:{finding_id}",
                message="The workflow changed after this decision; review the current route again.",
            ) for finding_id in sorted(stale_reviews))
        semantic_findings = [item for item in all_semantic_findings if item.identifier not in reviewed_finding_ids]
    for finding in semantic_findings:
        blockers.append(finding.message)
        blocker_details.append(ApprovalReadinessBlocker(
            kind="semantic_workflow", identifier=finding.identifier, message=finding.message,
        ))
    for finding in parsed.completeness_findings:
        if finding.approval_blocking and finding.status is not CompletenessStatus.KNOWN:
            blockers.append(finding.message)
            blocker_details.append(ApprovalReadinessBlocker(
                kind="completeness", identifier=finding.finding_id, message=finding.message,
            ))
    # Re-derive execution-critical absence from the content itself. A sparse
    # record cannot become approvable merely by omitting its review findings.
    derived_unknown = {
        "strategy.offer.summary": parsed.strategy.offer.summary,
        "strategy.offer.price_context": parsed.strategy.offer.price_context,
        "strategy.target_segments": parsed.strategy.target_segments or None,
        "strategy.commercial_goal": parsed.strategy.commercial_goal,
        "prospecting.classification_rules": parsed.prospecting.classification_rules or None,
        "setter_policy.forbidden_rules": parsed.setter_policy.forbidden_rules or None,
        "setter_policy.language_rules": parsed.setter_policy.language_rules or None,
        "setter_policy.handoff_conditions": parsed.setter_policy.handoff_conditions or None,
        "closer_policy.discovery_questions": parsed.closer_policy.discovery_questions or None,
        "closer_policy.fit_signals": parsed.closer_policy.fit_signals or None,
        "closer_policy.no_fit_signals": parsed.closer_policy.no_fit_signals or None,
        "closer_policy.objection_rules": parsed.closer_policy.objection_rules or None,
        "closer_policy.closing_rules": parsed.closer_policy.closing_rules or None,
        "channel_policies": parsed.channel_policies or None,
    }
    declared_findings = {item.finding_id for item in parsed.completeness_findings}
    for field_path, value in derived_unknown.items():
        finding_id = f"unknown:{field_path}"
        if value is None and finding_id not in declared_findings:
            blockers.append(f"Required source-backed content is unknown: {field_path}.")
            blocker_details.append(ApprovalReadinessBlocker(
                kind="completeness", identifier=finding_id,
                message=f"Required source-backed content is unknown: {field_path}.",
            ))
    for conflict in parsed.workflow_conflicts:
        if conflict.status == "needs_review":
            blockers.append(f"Unresolved workflow conflict: {conflict.stage_code.value}")
            blocker_details.append(ApprovalReadinessBlocker(
                kind="workflow_conflict", identifier=conflict.conflict_id,
            ))
    for conflict in parsed.source_conflicts:
        blockers.append(f"Source disagreement needs human review: {conflict.field}")
        blocker_details.append(ApprovalReadinessBlocker(
            kind="source_conflict", identifier=conflict.conflict_id,
            message=f"Seller profile and uploaded source differ for {conflict.field}.",
        ))
    if parsed.workflow_missing_required_conditions:
        missing = ", ".join(sorted(item.value for item in parsed.workflow_missing_required_conditions))
        blockers.append(f"Workflow is missing required transitions: {missing}")
        blocker_details.extend(ApprovalReadinessBlocker(
            kind="missing_required_transition", identifier=item.value,
        ) for item in parsed.workflow_missing_required_conditions)
    if policy.rejected_assertion_ids:
        # Rejected claims may remain visible, but cannot be compiled as authority.
        compiled = {item for trace in policy.compilation.compiled_rules for item in trace.source_assertion_ids}
        if compiled & set(policy.rejected_assertion_ids):
            blockers.append("Rejected assertion has executable authority")
            blocker_details.append(ApprovalReadinessBlocker(kind="safety", identifier="rejected_executable"))
    for item in policy.compilation.unresolved:
        if item.status is CompilationStatus.CONFLICT:
            blockers.append("Unresolved semantic contradiction")
            blocker_details.append(ApprovalReadinessBlocker(kind="safety", identifier="semantic_conflict"))
    required = list(dict.fromkeys(
        assertion_id for trace in policy.compilation.compiled_rules
        for assertion_id in trace.source_assertion_ids
    ))
    deduplicated = list(dict.fromkeys(blockers))
    missing_transitions = [MissingRequiredTransition(condition=item)
                           for item in parsed.workflow_missing_required_conditions]
    return ApprovalReadiness(
        blockers=deduplicated,
        required_guardrail_assertion_ids=required,
        blocker_details=blocker_details,
        missing_required_transitions=missing_transitions,
        ready_for_approval=not deduplicated,
    )


class ApprovedSetterStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: WorkflowStageCode
    step_number: int


class ApprovedSetterTransition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_stage: WorkflowStageCode
    to_stage: WorkflowStageCode
    condition: WorkflowConditionCode
    outcome: WorkflowOutcome
    maturity: PlaybookMaturity
    origin: str
    source_ref: str | None = None
    provenance_note: str | None = None


class ApprovedSetterWorkflow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stages: tuple[ApprovedSetterStage, ...]
    transitions: tuple[ApprovedSetterTransition, ...]


class ApprovedSetterGuardrail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PlaybookRuleCode
    effect: PolicyRuleEffect
    condition: WorkflowConditionCode | None
    maturity: PlaybookMaturity
    source_assertion_ids: tuple[str, ...]
    source_section_ids: tuple[str, ...]


class ApprovedSetterAssertion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion_id: str
    assertion_text: str
    modality: SemanticModality
    concept: SemanticConcept | None
    scope_text: str | None
    condition_text: str | None
    maturity: PlaybookMaturity | None
    source_section_ids: tuple[str, ...]


class ApprovedSetterContext(BaseModel):
    """A detached, read-only projection of approved authority for a future Setter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str
    workflow: ApprovedSetterWorkflow
    channel_policies: tuple[ChannelPolicy, ...] = ()
    compiled_guardrails: tuple[ApprovedSetterGuardrail, ...] = ()
    reviewed_assertions: tuple[ApprovedSetterAssertion, ...] = ()
    snapshot_digest: str | None = None


def verified_approved_semantic_playbook(playbook: SalesPlaybook) -> tuple[SalesPlaybookContentV3, str]:
    """Return v3 content only when it matches the persisted approval snapshot."""
    if playbook.status != SalesPlaybookStatus.APPROVED:
        raise ValueError("Only approved playbooks can provide semantic context")
    if playbook.content_contract_version != "3.0":
        raise ValueError("Unsupported approved playbook contract")
    # Approval already froze this exact authority. New safety gates apply to
    # new/current DRAFTs and must not retroactively invalidate old snapshots.
    readiness = approval_readiness(playbook.structured_content, semantic_safety=False)
    if readiness.blockers:
        raise ValueError("Unsafe approved semantic playbook")
    content = SalesPlaybookContentV3.model_validate(playbook.structured_content)
    validate_approved_workflow_structure(content.workflow)
    policy = content.semantic_policy
    audit = playbook.generation_audit
    snapshot = audit.get("approval_snapshot") if isinstance(audit, dict) else None
    reviewed = policy.reviewed_guardrail_assertion_ids
    if (not isinstance(snapshot, dict)
            or snapshot.get("version") != "3.0"
            or snapshot.get("reviewed_guardrail_assertion_ids") != sorted(reviewed)
            or set(reviewed) != set(readiness.required_guardrail_assertion_ids)
            or snapshot.get("digest") != _approved_digest(playbook.structured_content, reviewed)):
        raise ValueError("Approved semantic snapshot does not match reviewed authority")
    return content, snapshot["digest"]


def approved_setter_context(playbook: SalesPlaybook) -> ApprovedSetterContext:
    if playbook.status != SalesPlaybookStatus.APPROVED:
        raise ValueError("Only approved playbooks can provide Setter context")
    if playbook.content_contract_version == "2.0":
        legacy = SalesPlaybookContent.model_validate(playbook.structured_content)
        return ApprovedSetterContext(contract_version="2.0", workflow=_setter_workflow(legacy.workflow))
    content, snapshot_digest = verified_approved_semantic_playbook(playbook)
    if content.generic_workflow_graph is not None:
        raise ValueError("Setter does not support this generic workflow graph version")
    policy = content.semantic_policy
    reviewed = policy.reviewed_guardrail_assertion_ids
    traces = tuple(ApprovedSetterGuardrail(
        code=trace.rule.code, effect=trace.rule.effect, condition=trace.rule.condition,
        maturity=trace.rule.maturity, source_assertion_ids=tuple(trace.source_assertion_ids),
        source_section_ids=tuple(trace.source_section_ids),
    ) for trace in policy.compilation.compiled_rules)
    approved_ids = set(reviewed)
    assertions = tuple(ApprovedSetterAssertion(
        assertion_id=item.assertion_id, assertion_text=item.assertion_text,
        modality=item.modality, concept=item.concept, scope_text=item.scope_text,
        condition_text=item.condition_text, maturity=item.maturity,
        source_section_ids=tuple(item.source_section_ids),
    ) for item in policy.assertions if item.assertion_id in approved_ids)
    return ApprovedSetterContext(
        contract_version="3.0", workflow=_setter_workflow(content.workflow),
        channel_policies=tuple(content.channel_policies),
        compiled_guardrails=traces, reviewed_assertions=assertions,
        snapshot_digest=snapshot_digest,
    )


def _setter_workflow(workflow: Any) -> ApprovedSetterWorkflow:
    return ApprovedSetterWorkflow(
        stages=tuple(ApprovedSetterStage(code=stage.code, step_number=stage.step_number)
                     for stage in workflow.stages),
        transitions=tuple(ApprovedSetterTransition(
            from_stage=item.from_stage, to_stage=item.to_stage,
            condition=item.condition, outcome=item.outcome, maturity=item.maturity,
            origin=(item.provenance.origin.value if isinstance(item.provenance.origin, PlaybookOrigin)
                    else item.provenance.origin),
            source_ref=item.provenance.source_ref,
            provenance_note=item.provenance.note,
        ) for item in workflow.transitions),
    )


class SalesPlaybookService:
    def __init__(self, db: Session, generator: SalesPlaybookGenerator | None = None):
        self.db = db
        self.generator = generator or ContractReadyDraftGenerator()

    def _prepare_generation_context(
        self,
        *,
        workspace_id: UUID,
        source_type: SalesPlaybookSourceType,
        questionnaire: dict[str, Any] | None,
        pasted_text: str | None,
        source_document_ids: list[UUID] | None,
        source_documents: list[tuple[SourceDocument, str]],
        generation_payload: dict[str, Any],
        fingerprint: str | None,
        seller_profile_id: UUID | None,
        generation_job_id: UUID | None,
    ) -> _PreparedGenerationContext:
        """Reload and validate the exact generation inputs before issuing trust context."""
        profile = self._get_profile(workspace_id, seller_profile_id)
        if seller_profile_id is not None and profile is None:
            raise ValueError("Seller profile not found")

        semantic_source = source_type in (
            SalesPlaybookSourceType.MANUAL,
            SalesPlaybookSourceType.DOCUMENT,
        )
        source_conflicts = []
        source_input_sha256 = None
        if semantic_source:
            primary_source_text = generation_payload.get("pasted_text") or "\n\n".join(
                item[1] for item in source_documents
            )
            source_input_sha256 = sha256(primary_source_text.encode("utf-8")).hexdigest()
            source_text = primary_source_text + "\n\n" + "\n\n".join(
                item["content"] for item in generation_payload.get("additional_source_documents", [])
            )
            source_conflicts = detect_source_conflicts(
                profile,
                source_text,
                document_origin="source_document" if source_documents else "pasted_document",
            )

        source_section_map = []
        authorized_extraction_sections = []
        if source_documents:
            from app.modules.sales_playbook.semantic_playbook import SourceSectionReference
            from app.modules.sales_playbook.source_planning import (
                build_generic_extraction_plan,
                generic_source_sections,
            )

            extraction_sections = generic_source_sections([
                {"content": text, "document_id": str(item.id)}
                for item, text in source_documents
            ])
            planned_sections = [
                section
                for unit in build_generic_extraction_plan(extraction_sections)
                for section in unit.source_sections
            ]
            authorized_extraction_sections = planned_sections
            documents_by_id = {str(item.id): item for item, _text in source_documents}
            source_section_map = [
                SourceSectionReference(
                    extraction_section_id=section.fragment_id,
                    document_id=section.document_id,
                    original_section_id=section.section_id.split(":", 2)[2],
                    filename=documents_by_id[section.document_id].original_filename,
                    heading_path=list(section.heading_path),
                    start_line=section.start_line,
                    end_line=section.end_line,
                    fragment_id=section.fragment_id,
                    start_byte_offset=section.start_byte_offset,
                    end_byte_offset=section.end_byte_offset,
                )
                for section in planned_sections
            ]
        elif pasted_text:
            from app.modules.sales_playbook.semantic_playbook import SourceSectionReference
            from app.modules.sales_playbook.source_planning import (
                build_generic_extraction_plan,
                generic_source_sections,
            )

            extraction_sections = generic_source_sections([
                {"content": pasted_text, "document_id": None},
            ])
            planned_sections = [
                section
                for unit in build_generic_extraction_plan(extraction_sections)
                for section in unit.source_sections
            ]
            authorized_extraction_sections = planned_sections
            source_section_map = [
                SourceSectionReference(
                    extraction_section_id=section.fragment_id,
                    document_id=None,
                    original_section_id=section.section_id.split(":", 2)[2],
                    filename="Pasted sales documentation",
                    heading_path=list(section.heading_path),
                    start_line=section.start_line,
                    end_line=section.end_line,
                    fragment_id=section.fragment_id,
                    start_byte_offset=section.start_byte_offset,
                    end_byte_offset=section.end_byte_offset,
                )
                for section in planned_sections
            ]

        semantic_generate = getattr(self.generator, "generate_semantic_draft", None)
        if semantic_source and not callable(semantic_generate):
            raise SemanticGenerationUnavailable(
                "Semantic draft generation is unavailable; configure a semantic generator",
            )

        trusted_invocation = None
        if semantic_source:
            from app.modules.sales_playbook.generation_models import (
                GenerationJobStatus,
                SalesPlaybookGenerationJob,
            )
            from app.modules.sales_playbook.generation_service import (
                GenerationJobIntegrityError,
                generation_job_payload_matches_fingerprint,
                seller_profile_fingerprint,
            )
            from app.modules.sales_playbook.relation_invocation import (
                _issue_trusted_generation_invocation,
            )

            job = None
            if generation_job_id is not None:
                job = self.db.execute(select(SalesPlaybookGenerationJob).where(
                    SalesPlaybookGenerationJob.id == generation_job_id,
                    SalesPlaybookGenerationJob.workspace_id == workspace_id,
                )).scalar_one_or_none()
                if (job is None or job.status is not GenerationJobStatus.RUNNING
                        or not generation_job_payload_matches_fingerprint(job)):
                    raise GenerationJobIntegrityError()
                payload = SalesPlaybookDraftCreate.model_validate(job.request_payload)
                actual_document_ids = [str(record.id) for record, _ in source_documents]
                manifest_document_ids = [str(item["document_id"]) for item in job.source_manifest]
                actual_manifest = [{
                    "document_id": str(record.id),
                    "filename": record.original_filename,
                    "normalized_sha256": record.normalized_sha256,
                    "original_sha256": record.original_sha256,
                } for record, _ in source_documents]
                if (
                    payload.source_type != source_type
                    or payload.source_document_ids != (source_document_ids or [])
                    or actual_document_ids != manifest_document_ids
                    or actual_manifest != job.source_manifest
                    or (payload.pasted_text or None) != (pasted_text or None)
                    or (profile.id if profile is not None else None) != job.seller_profile_id
                    or seller_profile_fingerprint(profile) != job.seller_profile_fingerprint
                    or (questionnaire is not None and payload.questionnaire is None)
                    or (questionnaire is None and payload.questionnaire is not None)
                ):
                    raise GenerationJobIntegrityError()

            trusted_invocation = _issue_trusted_generation_invocation(
                workspace_id=workspace_id,
                generation_job=job,
                source_type=source_type.value,
                request_fingerprint=job.request_fingerprint if job is not None else fingerprint,
                source_input_sha256=source_input_sha256 or sha256(
                    json.dumps(questionnaire, sort_keys=True, ensure_ascii=False).encode("utf-8"),
                ).hexdigest(),
                source_documents=tuple(source_documents),
                seller_profile=profile,
                source_sections=tuple(authorized_extraction_sections),
                relation_contract_revision="relation_candidate_ir_v1",
            )

        return _PreparedGenerationContext(
            profile=profile,
            semantic_source=semantic_source,
            source_conflicts=tuple(source_conflicts),
            source_input_sha256=source_input_sha256,
            source_section_map=tuple(source_section_map),
            authorized_extraction_sections=tuple(authorized_extraction_sections),
            semantic_generator=semantic_generate,
            trusted_invocation=trusted_invocation,
        )

    def get(self, playbook_id: UUID, workspace_id: UUID) -> SalesPlaybook | None:
        return self.db.execute(
            select(SalesPlaybook).where(
                SalesPlaybook.id == playbook_id,
                SalesPlaybook.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list(self, workspace_id: UUID) -> list[SalesPlaybook]:
        return list(
            self.db.execute(
                select(SalesPlaybook)
                .where(SalesPlaybook.workspace_id == workspace_id)
                .order_by(SalesPlaybook.version.desc())
            ).scalars().all()
        )

    def get_active(self, workspace_id: UUID) -> SalesPlaybook | None:
        return self.db.execute(
            select(SalesPlaybook).where(
                SalesPlaybook.workspace_id == workspace_id,
                SalesPlaybook.status == SalesPlaybookStatus.APPROVED,
            )
        ).scalar_one_or_none()

    def create_draft(
        self,
        workspace_id: UUID,
        source_type: SalesPlaybookSourceType,
        questionnaire: dict[str, Any] | None,
        pasted_text: str | None,
        seller_profile_id: UUID | None,
        idempotency_key: str | None = None,
        source_document_ids: list[UUID] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
        release_transaction_before_generation: bool = False,
        generation_job_id: UUID | None = None,
    ) -> SalesPlaybook:
        if source_type == SalesPlaybookSourceType.QUESTIONNAIRE and source_document_ids:
            raise ValueError("Questionnaire drafts cannot include source documents")
        if source_type != SalesPlaybookSourceType.QUESTIONNAIRE and questionnaire is not None:
            raise ValueError("Questionnaire answers cannot be combined with manual sources")
        if source_document_ids and pasted_text:
            raise ValueError("Choose uploaded documents or pasted text, not both")
        source_documents = self._load_source_documents(workspace_id, source_document_ids or [])
        persisted_payload = {"questionnaire": questionnaire} if questionnaire else (
            {"source_document_manifest": [
                {"id": str(item.id), "filename": item.original_filename,
                 "original_sha256": item.original_sha256, "normalized_sha256": item.normalized_sha256}
                for item, _ in source_documents
            ]} if source_documents else {"pasted_text": pasted_text}
        )
        generation_payload = {"questionnaire": questionnaire} if questionnaire else {"pasted_text": pasted_text}
        if source_documents:
            generation_payload = {
                "source_documents": [
                    {"filename": item.original_filename, "document_id": str(item.id), "content": text,
                     "sections": item.sections}
                    for item, text in source_documents
                ],
            }
        elif pasted_text:
            generation_payload = {"pasted_text": pasted_text}
        fingerprint = _fingerprint(source_type, {
            "questionnaire": questionnaire, "pasted_text": pasted_text,
            "source_documents": [(str(item.id), item.normalized_sha256) for item, _ in source_documents],
        }, seller_profile_id) if idempotency_key else None
        if idempotency_key:
            existing = self._get_by_key(workspace_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, fingerprint)
        if source_type == SalesPlaybookSourceType.QUESTIONNAIRE and pasted_text:
            raise ValueError("Questionnaire drafts cannot include pasted manual text")
        context = self._prepare_generation_context(
            workspace_id=workspace_id,
            source_type=source_type,
            questionnaire=questionnaire,
            pasted_text=pasted_text,
            source_document_ids=source_document_ids,
            source_documents=source_documents,
            generation_payload=generation_payload,
            fingerprint=fingerprint,
            seller_profile_id=seller_profile_id,
            generation_job_id=generation_job_id,
        )
        profile = context.profile
        semantic_source = context.semantic_source
        source_conflicts = list(context.source_conflicts)
        source_input_sha256 = context.source_input_sha256
        source_section_map = list(context.source_section_map)
        semantic_generate = context.semantic_generator
        trusted_invocation = context.trusted_invocation
        try:
            if semantic_source:
                if release_transaction_before_generation:
                    for record, _ in source_documents:
                        self.db.expunge(record)
                    if profile is not None:
                        self.db.expunge(profile)
                    self.db.rollback()
                if progress_callback is None:
                    generated = semantic_generate(
                        profile, source_type, generation_payload,
                        trusted_invocation=trusted_invocation,
                    )
                else:
                    generated = semantic_generate(
                        profile, source_type, generation_payload,
                        progress_callback=progress_callback,
                        trusted_invocation=trusted_invocation,
                    )
                content = SalesPlaybookContentV3.model_validate(generated)
                content = content.model_copy(update={
                    "semantic_safety_version": "6.3",
                    "source_input_sha256": source_input_sha256,
                })
                if source_section_map:
                    content = content.model_copy(update={"source_section_map": source_section_map})
                if source_documents:
                    content = content.model_copy(update={
                        "source_documents": [SourceDocumentReference(
                            document_id=str(item.id), filename=item.original_filename,
                            original_sha256=item.original_sha256,
                            normalized_sha256=item.normalized_sha256,
                        ) for item, _ in source_documents],
                    })
                if source_conflicts:
                    content = content.model_copy(update={"source_conflicts": source_conflicts})
            else:
                if release_transaction_before_generation:
                    if profile is not None:
                        self.db.expunge(profile)
                    self.db.rollback()
                if progress_callback is not None:
                    progress_callback("analysis", 0, 1)
                generated = self.generator.generate(profile, source_type, generation_payload)
                if progress_callback is not None:
                    progress_callback("analysis", 1, 1)
                content = SalesPlaybookContent.model_validate(generated)
            serialized = content.model_dump(mode="json")
            # Verify the exact JSON representation that will be persisted.
            if content.contract_version == "3.0":
                SalesPlaybookContentV3.model_validate(serialized)
            else:
                SalesPlaybookContent.model_validate(serialized)
            audit = getattr(self.generator, "generation_audit", None)
            if audit is not None and not audit.success:
                raise ValueError("Sales playbook generation was not successful")
        except Exception as exc:
            phase = _safe_phase(getattr(self.generator, "current_phase", None))
            if phase == "semantic_playbook_assembly" and isinstance(exc, ValidationError):
                original_error = exc
                metrics = _safe_assembly_metrics(
                    getattr(self.generator, "last_assembly_metrics", None),
                ) or {}
                exc = PlaybookAssemblyValidationError.from_exception(original_error, metrics)
                exc.__cause__ = original_error
                exc.__traceback__ = original_error.__traceback__
            local_phase = (
                phase in {
                    "workflow_assembly", "semantic_owner_merge",
                    "supporting_section_mapping", "supporting_section_selection",
                    "supporting_provenance_validation", "semantic_playbook_assembly",
                }
                or phase.startswith("supporting_contract_preparation:")
            )
            attempt_ref = sha256(
                (idempotency_key or fingerprint or "unkeyed").encode("utf-8")
            ).hexdigest()[:16]
            frames = traceback.extract_tb(exc.__traceback__)[-8:]
            safe_stack = [
                {
                    "file": frame.filename.rsplit("/", 1)[-1][:100],
                    "function": re.sub(r"[^A-Za-z0-9_.<>-]", "_", frame.name)[:100],
                    "line": frame.lineno,
                }
                for frame in frames
            ]
            logger.error(
                "Sales playbook generation rejected phase=%s category=%s "
                "exception_type=%s safe_message=%s attempt_ref=%s stack=%s diagnostics=%s",
                phase,
                "local_processing" if local_phase else "generation_or_provider",
                type(exc).__name__,
                _safe_exception_message(exc, phase),
                attempt_ref,
                safe_stack,
                _safe_failure_diagnostics(self.generator),
            )
            if local_phase:
                raise LocalSalesPlaybookProcessingError(
                    "Sales playbook processing failed"
                ) from exc
            raise SalesPlaybookGenerationError("Sales playbook generation failed") from exc
        audit = getattr(self.generator, "generation_audit", None)
        # Concurrent retries may both generate; the lock and unique key ensure one stored draft.
        self.db.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()).scalar_one()
        if idempotency_key:
            existing = self._get_by_key(workspace_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, fingerprint)
        version = (self.db.scalar(
            select(func.max(SalesPlaybook.version)).where(SalesPlaybook.workspace_id == workspace_id)
        ) or 0) + 1
        playbook = SalesPlaybook(
            workspace_id=workspace_id,
            seller_profile_id=profile.id if profile else None,
            version=version,
            status=SalesPlaybookStatus.DRAFT,
            source_type=source_type,
            structured_content=serialized,
            content_contract_version=content.contract_version,
            source_payload=persisted_payload,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            model_name=audit.model if audit else None,
            prompt_version=audit.prompt_version if audit else "sales-playbook-contract-v1",
            generation_provider=audit.provider if audit else "deterministic",
            generation_operation=audit.operation if audit else "sales_playbook_generation",
            generation_latency_ms=audit.latency_ms if audit else None,
            generation_success=audit.success if audit else True,
            generation_tokens=audit.tokens if audit else None,
            generation_audit=_safe_audit(audit),
        )
        self.db.add(playbook)
        self.db.flush()
        return playbook

    def _load_source_documents(self, workspace_id: UUID, document_ids: list[UUID]):
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Duplicate source document ID")
        if not document_ids:
            return []
        records = list(self.db.execute(select(SourceDocument).where(
            SourceDocument.workspace_id == workspace_id,
            SourceDocument.id.in_(document_ids),
        )).scalars().all())
        by_id = {item.id: item for item in records}
        if len(by_id) != len(document_ids):
            raise ValueError("Source document not found in this workspace")
        from app.integrations.storage import StorageProvider
        storage = StorageProvider()
        import hashlib
        result = []
        total_bytes = 0
        for document_id in document_ids:
            record = by_id[document_id]
            normalized = storage.download(record.normalized_object_key)
            total_bytes += len(normalized)
            if total_bytes > MAX_AGGREGATE_NORMALIZED_SOURCE_BYTES:
                raise ValueError("Combined normalized source documents exceed 2 MiB")
            if hashlib.sha256(normalized).hexdigest() != record.normalized_sha256:
                raise ValueError("Normalized source document integrity check failed")
            result.append((record, normalized.decode("utf-8")))
        return result

    def _get_by_key(self, workspace_id: UUID, key: str) -> SalesPlaybook | None:
        return self.db.execute(select(SalesPlaybook).where(
            SalesPlaybook.workspace_id == workspace_id, SalesPlaybook.idempotency_key == key,
        )).scalar_one_or_none()

    @staticmethod
    def _replay(existing: SalesPlaybook, fingerprint: str) -> SalesPlaybook:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflict("Idempotency key already used with different input")
        return existing

    def approve(
        self, playbook_id: UUID, workspace_id: UUID, approved_by: UUID,
        reviewed_guardrail_assertion_ids: list[str] | None = None,
        reviewed_content_digest: str | None = None,
        *, commercial_confirmed: bool = False,
    ) -> SalesPlaybook | None:
        self.db.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()).scalar_one_or_none()
        playbook = self.db.execute(select(SalesPlaybook).where(
            SalesPlaybook.id == playbook_id,
            SalesPlaybook.workspace_id == workspace_id,
        ).execution_options(populate_existing=True)).scalar_one_or_none()
        if playbook is None:
            return None
        if playbook.status != SalesPlaybookStatus.DRAFT:
            raise ValueError("Only draft playbooks can be approved")

        if playbook.content_contract_version == "4.0":
            if not commercial_confirmed:
                raise ValueError("Commercial context approval requires confirmed=true")
            if reviewed_guardrail_assertion_ids:
                raise ValueError("Commercial context approval does not accept semantic acknowledgments")
            from app.modules.sales_playbook.commercial_service import CommercialContextService

            return CommercialContextService(self.db).approve_commercial(
                playbook_id=playbook_id,
                workspace_id=workspace_id,
                approved_by=approved_by,
                confirmed=commercial_confirmed,
                content_digest=reviewed_content_digest or "",
                workspace_locked=True,
            )

        approved_content = None
        snapshot = None
        if playbook.content_contract_version == "3.0":
            readiness = approval_readiness(playbook.structured_content)
            if readiness.blockers:
                raise ValueError("Semantic playbook approval blocked: " + "; ".join(readiness.blockers))
            if playbook.generation_success is False or (playbook.generation_audit or {}).get("success") is False:
                raise ValueError("Semantic playbook generation was incomplete")
            audit = playbook.generation_audit or {}
            if any(item.get("success") is False or item.get("finish_reason") == "length"
                   for item in audit.get("extractions", []) if isinstance(item, dict)):
                raise ValueError("Semantic playbook extraction was incomplete or truncated")
            ids = reviewed_guardrail_assertion_ids or []
            required = readiness.required_guardrail_assertion_ids
            if len(ids) != len(set(ids)) or set(ids) != set(required):
                raise ValueError("Guardrail acknowledgments must match every compiled critical assertion exactly")
            if reviewed_content_digest != content_digest(playbook.structured_content):
                raise ValueError("Reviewed playbook content changed; review the current draft")
            candidate = SalesPlaybookContentV3.model_validate(playbook.structured_content)
            if candidate.source_input_sha256:
                if candidate.source_documents:
                    if candidate.source_input_sha256 != candidate.source_documents[0].normalized_sha256:
                        raise ValueError("Primary source document differs from the reviewed extraction input")
                else:
                    pasted_source = (playbook.source_payload or {}).get("pasted_text")
                    if not isinstance(pasted_source, str) or sha256(pasted_source.encode("utf-8")).hexdigest() != candidate.source_input_sha256:
                        raise ValueError("Pasted source changed after human review; generate a new draft")
            self._verify_source_documents(workspace_id, candidate.source_documents)
            validate_approved_workflow_structure(candidate.workflow)
            candidate.semantic_policy.reviewed_guardrail_assertion_ids = sorted(ids)
            approved_content = candidate.model_dump(mode="json")
            # Revalidate after the approval-only review IDs are attached.
            SalesPlaybookContentV3.model_validate(approved_content)
            snapshot = {
                "version": "3.0",
                "reviewed_guardrail_assertion_ids": sorted(ids),
                "digest": _approved_digest(approved_content, ids),
            }
        elif playbook.content_contract_version == "2.0":
            SalesPlaybookContent.model_validate(playbook.structured_content)
        else:
            raise ValueError("Unsupported playbook contract")

        active = self.get_active(workspace_id)
        if active is not None and active.id != playbook.id:
            active.status = SalesPlaybookStatus.ARCHIVED
            active.updated_at = datetime.now(UTC)
            self.db.flush()

        if approved_content is not None:
            playbook.structured_content = approved_content
            playbook.generation_audit = {**(playbook.generation_audit or {}), "approval_snapshot": snapshot}
        playbook.status = SalesPlaybookStatus.APPROVED
        playbook.approved_by = approved_by
        playbook.approved_at = datetime.now(UTC)
        playbook.updated_at = datetime.now(UTC)
        self.db.flush()
        return playbook

    def _verify_source_documents(
        self,
        workspace_id: UUID,
        references: list[SourceDocumentReference],
        *,
        storage=None,
    ) -> None:
        if not references:
            return
        reference_ids = [UUID(item.document_id) for item in references]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("Source document references are duplicated")
        records = self.db.execute(select(SourceDocument).where(
            SourceDocument.workspace_id == workspace_id,
            SourceDocument.id.in_(reference_ids),
        )).scalars().all()
        by_id = {item.id: item for item in records}
        if len(by_id) != len(reference_ids):
            raise ValueError("Reviewed source document is no longer available in this workspace")
        if storage is None:
            from app.integrations.storage import StorageProvider

            storage = StorageProvider()
        for reference in references:
            record = by_id[UUID(reference.document_id)]
            if (
                record.normalization_status != "complete"
                or record.original_filename != reference.filename
                or record.original_sha256 != reference.original_sha256
                or record.normalized_sha256 != reference.normalized_sha256
            ):
                raise ValueError("Source document changed after human review; generate a new draft")
            try:
                original = storage.download(record.original_object_key)
                normalized = storage.download(record.normalized_object_key)
            except Exception as exc:
                raise ValueError("Reviewed source document could not be verified") from exc
            if sha256(original).hexdigest() != reference.original_sha256 or sha256(normalized).hexdigest() != reference.normalized_sha256:
                raise ValueError("Source document changed after human review; generate a new draft")

    def resolve_workflow_conflict(
        self,
        playbook_id: UUID,
        workspace_id: UUID,
        reviewer_id: UUID,
        conflict_id: str,
        candidate_id: str,
    ) -> SalesPlaybook | None:
        self.db.execute(select(Workspace.id).where(
            Workspace.id == workspace_id,
        ).with_for_update()).scalar_one_or_none()
        playbook = self.get(playbook_id, workspace_id)
        if playbook is None:
            return None
        if playbook.status != SalesPlaybookStatus.DRAFT:
            raise ValueError("Only draft playbooks can resolve workflow conflicts")
        if playbook.content_contract_version != "3.0":
            raise ValueError("Workflow conflict resolution requires contract v3")
        content = SalesPlaybookContentV3.model_validate(playbook.structured_content)
        resolved = resolve_semantic_workflow_conflict(
            content,
            conflict_id=conflict_id,
            candidate_id=candidate_id,
            reviewer_id=str(reviewer_id),
        )
        playbook.structured_content = resolved.model_dump(mode="json")
        playbook.updated_at = datetime.now(UTC)
        self.db.flush()
        return playbook

    def complete_workflow_transition(
        self,
        playbook_id: UUID,
        workspace_id: UUID,
        reviewer_id: UUID,
        transition: SalesPlaybookWorkflowTransitionComplete,
    ) -> SalesPlaybook | None:
        self.db.execute(select(Workspace.id).where(
            Workspace.id == workspace_id,
        ).with_for_update()).scalar_one_or_none()
        playbook = self.get(playbook_id, workspace_id)
        if playbook is None:
            return None
        if playbook.status != SalesPlaybookStatus.DRAFT:
            raise ValueError("Only draft playbooks can complete workflow transitions")
        if playbook.content_contract_version != "3.0":
            raise ValueError("Human workflow completion requires contract v3")
        content = SalesPlaybookContentV3.model_validate(playbook.structured_content)
        completed = complete_required_workflow_transition(
            content, transition=transition, reviewer_id=str(reviewer_id),
        )
        playbook.structured_content = completed.model_dump(mode="json")
        playbook.updated_at = datetime.now(UTC)
        self.db.flush()
        return playbook

    def review_semantic_workflow_finding(
        self, playbook_id: UUID, workspace_id: UUID, reviewer_id: UUID, finding_id: str,
    ) -> SalesPlaybook | None:
        self.db.execute(select(Workspace.id).where(
            Workspace.id == workspace_id,
        ).with_for_update()).scalar_one_or_none()
        playbook = self.get(playbook_id, workspace_id)
        if playbook is None:
            return None
        if playbook.status != SalesPlaybookStatus.DRAFT or playbook.content_contract_version != "3.0":
            raise ValueError("Semantic workflow review requires a v3 DRAFT")
        content = SalesPlaybookContentV3.model_validate(playbook.structured_content)
        findings = validate_workflow_semantics(content.workflow.transitions, content.semantic_policy.assertions)
        if not any(item.identifier == finding_id for item in findings):
            raise ValueError("Semantic workflow finding is not present in this DRAFT")
        if any(item.finding_id == finding_id for item in content.semantic_policy.workflow_reviews):
            raise ValueError("Semantic workflow finding was already reviewed")
        content.semantic_policy.workflow_reviews.append(SemanticWorkflowReview(
            finding_id=finding_id,
            action="accept_current_interpretation",
            reviewer_id=str(reviewer_id),
            reviewed_at=datetime.now(UTC),
        ))
        playbook.structured_content = content.model_dump(mode="json")
        playbook.updated_at = datetime.now(UTC)
        self.db.flush()
        return playbook

    def archive(self, playbook_id: UUID, workspace_id: UUID) -> SalesPlaybook | None:
        playbook = self.get(playbook_id, workspace_id)
        if playbook is None:
            return None
        if playbook.status == SalesPlaybookStatus.ARCHIVED:
            return playbook
        playbook.status = SalesPlaybookStatus.ARCHIVED
        playbook.updated_at = datetime.now(UTC)
        self.db.flush()
        return playbook

    def reject(
        self,
        playbook_id: UUID,
        workspace_id: UUID,
        rejected_by: UUID,
        reason: str | None,
    ) -> SalesPlaybook | None:
        playbook = self.get(playbook_id, workspace_id)
        if playbook is None:
            return None
        if playbook.status != SalesPlaybookStatus.DRAFT:
            raise ValueError("Only draft playbooks can be rejected")

        playbook.status = SalesPlaybookStatus.REJECTED
        playbook.rejected_by = rejected_by
        playbook.rejected_at = datetime.now(UTC)
        playbook.rejection_reason = reason.strip() if reason and reason.strip() else None
        playbook.updated_at = datetime.now(UTC)
        self.db.flush()
        return playbook

    def _get_profile(self, workspace_id: UUID, seller_profile_id: UUID | None) -> SellerProfile | None:
        query = select(SellerProfile).where(SellerProfile.workspace_id == workspace_id)
        if seller_profile_id is not None:
            query = query.where(SellerProfile.id == seller_profile_id)
        return self.db.execute(query).scalar_one_or_none()
