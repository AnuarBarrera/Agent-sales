"""Preflight, one-call Setter generation, validation and persistence."""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.contracts import QualificationStatus
from app.core.config import get_settings
from app.integrations.llm import NebiusTokenFactoryProvider
from app.modules.prospect_research.models import (
    ProspectResearch,
    ProspectResearchStatus,
    ResearchEvidence,
)
from app.modules.sales_playbook.schemas import PlaybookRuleCode, PolicyRuleEffect
from app.modules.setter.context import (
    ApprovedCommercialSetterContext,
    SetterContext,
    SetterPreconditionError,
    SetterPreflightResult,
    build_setter_context,
)
from app.modules.setter.generator import SetterGenerationAudit, SetterGenerator
from app.modules.setter.models import SetterExecution
from app.modules.setter.schemas import (
    SetterDecision,
    SetterDecisionAction,
    SetterDraftReadinessRead,
    SetterModelOutput,
)

logger = logging.getLogger(__name__)
_PRICE_PATTERNS = (
    re.compile(r"(?:[$€£]\s*\d[\d.,]*(?:\s*(?:MXN|USD|EUR|GBP))?)", re.IGNORECASE),
    re.compile(r"\b\d[\d.,]*\s*(?:MXN|USD|EUR|GBP|pesos?|dólares?|euros?|libras)\b", re.IGNORECASE),
    re.compile(r"\b(?:MXN|USD|EUR|GBP)\s*\d[\d.,]*\b", re.IGNORECASE),
)


class SetterService:
    """Execute Setter only from verified server-built context."""

    def __init__(
        self,
        db: Session,
        provider_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.db = db
        # Kept injectable for the generation service. Preflight deliberately
        # never invokes it, including deterministic qualification branches.
        self.provider_factory = provider_factory

    def preview_draft_readiness(self, lead_id: UUID, workspace_id: UUID) -> SetterDraftReadinessRead:
        """Read current server authority and decide draft purpose without writes."""
        from app.modules.lead_fit.projection import (
            FitProjectionError,
            load_current_fit_snapshot,
        )
        from app.modules.sales_playbook.commercial_context import (
            verified_approved_commercial_context,
        )
        from app.modules.sales_playbook.commercial_service import CommercialContextService
        from app.modules.sales_playbook.models import SalesPlaybook, SalesPlaybookStatus
        from app.modules.setter.draft_readiness import evaluate_draft_readiness

        playbook = self.db.execute(
            select(SalesPlaybook).where(
                SalesPlaybook.workspace_id == workspace_id,
                SalesPlaybook.status == SalesPlaybookStatus.APPROVED,
            ).execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if playbook is None or playbook.content_contract_version != "4.0":
            return SetterDraftReadinessRead(
                draft_ready=False, reason_codes=["authority_unavailable"],
                identity_status="unresolved", contact_channels=[], lead_id=lead_id,
            )
        try:
            authority = verified_approved_commercial_context(playbook)
            CommercialContextService(self.db).validate_commercial_bindings(
                workspace_id, authority.source_snapshot, playbook.source_payload,
            )
            snapshot = load_current_fit_snapshot(
                self.db, workspace_id=workspace_id, lead_id=lead_id, playbook_id=playbook.id,
                authorized_channels=tuple(authority.communication.allowed_channels),
            )
            readiness = evaluate_draft_readiness(
                policy=snapshot.policy.content, result=snapshot.result, dossier=snapshot.dossier,
                authorized_channels=tuple(authority.communication.allowed_channels),
            )
        except (FitProjectionError, ValueError, TypeError) as exc:
            message = str(exc).lower()
            if "source document" in message or "reviewed source" in message:
                reason = "authority_unavailable"
            elif "research is authorized" in message or "research grant" in message:
                reason = "research_active"
            elif "policy" in message or "authority" in message:
                reason = "authority_unavailable"
            elif "identity" in message or "dossier" in message:
                reason = "identity_unresolved"
            elif "current" in message or "digest" in message or "stale" in message:
                reason = "evaluation_stale"
            else:
                reason = "evaluation_unavailable"
            return SetterDraftReadinessRead(
                draft_ready=False, reason_codes=[reason],
                identity_status="unresolved", contact_channels=[], lead_id=lead_id,
            )
        return SetterDraftReadinessRead(
            draft_ready=readiness.draft_ready,
            draft_purpose=readiness.draft_purpose,
            reason_codes=list(readiness.reason_codes),
            identity_status=readiness.identity_status,
            contact_channels=list(readiness.contact_channels),
            lead_id=lead_id,
            evaluation_id=snapshot.evaluation.id,
            policy_id=snapshot.policy.id,
            playbook_id=playbook.id,
            research_id=snapshot.dossier.research_id,
            policy_digest=snapshot.evaluation.policy_digest,
            source_digest=snapshot.evaluation.source_digest,
            result_digest=snapshot.evaluation.result_digest,
        )

    def preflight(self, lead_id: UUID, workspace_id: UUID) -> SetterPreflightResult:
        context: SetterContext = build_setter_context(self.db, lead_id, workspace_id)
        if context.qualification.decision is QualificationStatus.NOT_QUALIFIED:
            return SetterPreflightResult(
                action=SetterDecisionAction.DO_NOT_CONTACT,
                rationale="The persisted qualification decision is not qualified.",
                context=context,
            )
        if context.qualification.decision is QualificationStatus.NEEDS_MORE_RESEARCH:
            if context.draft_purpose == "discovery":
                return SetterPreflightResult(
                    action=SetterDecisionAction.DRAFT_TOUCH_1,
                    rationale="Approved contact evidence permits a discovery question; opportunity remains unresolved.",
                    context=context,
                )
            return SetterPreflightResult(
                action=SetterDecisionAction.NEEDS_HUMAN_REVIEW,
                rationale="The persisted qualification decision requires more research.",
                context=context,
            )
        return SetterPreflightResult(
            action=SetterDecisionAction.DRAFT_TOUCH_1,
            rationale="The persisted qualification decision permits a Touch 1 draft.",
            context=context,
        )

    def execute(
        self,
        lead_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        *,
        channel: Literal["email", "phone"] | None = None,
    ) -> SetterExecution:
        preflight = self.preflight(lead_id, workspace_id)
        context = _restrict_channel(preflight.context, channel)
        preflight = preflight.model_copy(update={"context": context})
        context_digest = _context_digest(context)
        idempotency_key = _idempotency_key(context, channel=channel)
        existing = self._get_existing(context, idempotency_key)
        if existing is not None:
            if isinstance(context.approved_context, ApprovedCommercialSetterContext):
                stored_decision = getattr(existing, "decision", None)
                stored_context_digest = (
                    stored_decision.get("context_digest")
                    if isinstance(stored_decision, dict) else None
                )
                if stored_context_digest != context_digest:
                    raise SetterOutputValidationError(
                        "Existing Setter decision has a stale context digest"
                    )
            return existing

        audit: SetterGenerationAudit | None = None
        if preflight.action is SetterDecisionAction.DRAFT_TOUCH_1:
            if context.draft_purpose == "discovery":
                from app.modules.setter.discovery import build_discovery_output

                output = build_discovery_output(context)
            else:
                provider = self._create_provider()
                output, audit = SetterGenerator(provider).generate(context)
        else:
            output = _deterministic_output(preflight)

        _validate_output(context, output)
        validation_status, findings = _validate_supported_guardrails(context, output)

        # Rebuild from current database state immediately before persistence.
        # A playbook/evidence change during inference invalidates this result.
        current_context = build_setter_context(self.db, lead_id, workspace_id)
        try:
            current_context = _restrict_channel(current_context, channel)
        except ValueError as exc:
            raise SetterOutputValidationError(
                "Approved context changed during generation",
            ) from exc
        if _context_digest(current_context) != context_digest:
            raise SetterOutputValidationError("Approved context changed during generation")

        now = datetime.now(UTC)
        decision = SetterDecision(
            id=uuid4(),
            workspace_id=context.workspace_id,
            lead_id=context.lead_id,
            analysis_id=context.analysis_id,
            research_id=context.research_id,
            playbook_id=context.playbook_id,
            playbook_version=context.playbook_version,
            playbook_digest=context.playbook_digest,
            workflow_stage=context.workflow_stage.value,
            action=output.action,
            draft_purpose=context.draft_purpose,
            output=output,
            evidence_ids=output.evidence_ids,
            approved_guardrail_ids=output.guardrail_ids,
            approved_assertion_ids=output.assertion_ids,
            provider=audit.provider if audit else None,
            model=audit.model if audit else None,
            context_digest=context_digest,
            created_at=now,
        )
        generation_metadata = _generation_metadata(audit)
        validation_result = {
            "status": validation_status,
            "findings": findings,
            "generation": generation_metadata,
        }
        execution = SetterExecution(
            id=decision.id,
            workspace_id=workspace_id,
            lead_id=lead_id,
            analysis_id=context.analysis_id,
            research_id=context.research_id,
            playbook_id=context.playbook_id,
            created_by_id=user_id,
            playbook_version=context.playbook_version,
            playbook_digest=context.playbook_digest,
            workflow_stage=context.workflow_stage.value,
            idempotency_key=idempotency_key,
            action=output.action.value,
            decision=decision.model_dump(mode="json"),
            draft=decision.touch1_draft.model_dump(mode="json") if decision.touch1_draft else None,
            validation_result=validation_result,
            provider=audit.provider if audit else None,
            model=audit.model if audit else None,
            provider_request_id=audit.response_id if audit else None,
            latency_ms=audit.latency_ms if audit else None,
            token_count=(audit.total_tokens if audit else None),
            review_status="pending_human_review",
        )
        try:
            with self.db.begin_nested():
                self.db.add(execution)
                self.db.flush()
        except IntegrityError:
            # A concurrent identical execution may have won the unique-key race.
            winner = self._get_existing(context, idempotency_key)
            if winner is not None:
                return winner
            raise
        return execution

    def _create_provider(self) -> Any:
        if self.provider_factory is not None:
            return self.provider_factory()
        settings = get_settings()
        if not settings.nebius_enabled or not settings.nebius_api_key:
            raise RuntimeError("Nebius is disabled or missing API credentials")
        return NebiusTokenFactoryProvider(settings)

    def _get_existing(self, context: SetterContext, idempotency_key: str) -> SetterExecution | None:
        return self.db.execute(
            select(SetterExecution).where(
                SetterExecution.workspace_id == context.workspace_id,
                SetterExecution.lead_id == context.lead_id,
                SetterExecution.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()


class SetterOutputValidationError(ValueError):
    """Untrusted structured output failed deterministic authorization checks."""


class SetterEvidenceUnavailable(ValueError):
    """Persisted Setter citations cannot be resolved safely to their snapshots."""


def list_execution_evidence(db: Session, execution: SetterExecution) -> list[ResearchEvidence]:
    """Resolve only stored citations within this execution's completed snapshot ancestry."""
    try:
        decision = SetterDecision.model_validate(execution.decision)
    except (ValidationError, TypeError, ValueError) as exc:
        raise SetterEvidenceUnavailable("Setter evidence is unavailable for this execution") from exc
    if (
        decision.workspace_id != execution.workspace_id
        or decision.lead_id != execution.lead_id
        or decision.research_id != execution.research_id
    ):
        raise SetterEvidenceUnavailable("Setter evidence is unavailable for this execution")

    snapshot_ids: list[UUID] = []
    seen_snapshots: set[UUID] = set()
    cursor_id: UUID | None = execution.research_id
    while cursor_id is not None:
        if cursor_id in seen_snapshots or len(snapshot_ids) >= 20:
            raise SetterEvidenceUnavailable("Setter evidence ancestry is cyclic or too deep")
        snapshot = db.execute(
            select(ProspectResearch).where(
                ProspectResearch.id == cursor_id,
                ProspectResearch.workspace_id == execution.workspace_id,
                ProspectResearch.lead_id == execution.lead_id,
                ProspectResearch.status == ProspectResearchStatus.COMPLETED,
            )
        ).scalar_one_or_none()
        if snapshot is None or (
            snapshot.id != cursor_id
            or snapshot.workspace_id != execution.workspace_id
            or snapshot.lead_id != execution.lead_id
            or snapshot.status != ProspectResearchStatus.COMPLETED
        ):
            raise SetterEvidenceUnavailable("Setter evidence ancestry is missing or invalid")
        seen_snapshots.add(snapshot.id)
        snapshot_ids.append(snapshot.id)
        cursor_id = snapshot.refresh_of_id

    citation_ids = decision.evidence_ids
    rows = db.execute(
        select(ResearchEvidence).where(
            ResearchEvidence.research_id.in_(snapshot_ids),
            ResearchEvidence.workspace_id == execution.workspace_id,
            ResearchEvidence.lead_id == execution.lead_id,
            ResearchEvidence.evidence_id.in_(citation_ids),
        )
    ).scalars().all()
    evidence_by_id: dict[str, ResearchEvidence] = {}
    for row in rows:
        if (
            row.workspace_id != execution.workspace_id
            or row.lead_id != execution.lead_id
            or row.research_id not in seen_snapshots
            or row.evidence_id not in citation_ids
            or row.evidence_id in evidence_by_id
        ):
            raise SetterEvidenceUnavailable("Setter evidence citations are ambiguous or invalid")
        evidence_by_id[row.evidence_id] = row

    if set(evidence_by_id) != set(citation_ids):
        raise SetterEvidenceUnavailable("Setter evidence cited by this execution is unavailable")
    return [evidence_by_id[citation_id] for citation_id in citation_ids]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _context_digest(context: SetterContext) -> str:
    material = context.model_dump(mode="json")
    if context.draft_purpose != "discovery":
        material.pop("draft_purpose", None)
    else:
        from app.modules.setter.discovery import DISCOVERY_TEMPLATE_VERSION

        material["discovery_template_version"] = DISCOVERY_TEMPLATE_VERSION
    if context.fit_evaluation_id is None:
        for field in (
            "fit_evaluation_id", "fit_policy_id", "fit_policy_digest",
            "fit_source_digest", "fit_result_digest",
        ):
            material.pop(field, None)
    return hashlib.sha256(_canonical_json(material)).hexdigest()


def _restrict_channel(context: SetterContext, channel: str | None) -> SetterContext:
    if channel is None:
        return context
    if channel not in {"email", "phone"}:
        raise ValueError("Requested Setter channel is not authorized")
    approved = context.approved_context
    if not isinstance(approved, ApprovedCommercialSetterContext):
        raise ValueError("Requested Setter channel requires approved commercial authority")
    if channel not in approved.authorized_channels:
        raise ValueError("Requested Setter channel is not authorized")
    restricted = approved.model_copy(update={"authorized_channels": (channel,)})
    return context.model_copy(update={"approved_context": restricted})


def _idempotency_key(
    context: SetterContext,
    *,
    channel: Literal["email", "phone"] | None = None,
) -> str:
    material = {
        "workspace_id": str(context.workspace_id),
        "lead_id": str(context.lead_id),
        "analysis_id": str(context.analysis_id),
        "playbook_digest": context.playbook_digest,
        "workflow_stage": context.workflow_stage.value,
    }
    if context.fit_evaluation_id is not None:
        material.update({
            "fit_evaluation_id": str(context.fit_evaluation_id),
            "fit_policy_id": str(context.fit_policy_id),
            "fit_policy_digest": context.fit_policy_digest,
            "fit_source_digest": context.fit_source_digest,
            "fit_result_digest": context.fit_result_digest,
        })
    if context.draft_purpose == "discovery":
        from app.modules.setter.discovery import DISCOVERY_TEMPLATE_VERSION

        material.update({
            "draft_purpose": context.draft_purpose,
            "discovery_template_version": DISCOVERY_TEMPLATE_VERSION,
        })
    if channel is not None:
        material["channel"] = channel
    return hashlib.sha256(_canonical_json(material)).hexdigest()


def _deterministic_output(preflight: SetterPreflightResult) -> SetterModelOutput:
    context = preflight.context
    if isinstance(context.approved_context, ApprovedCommercialSetterContext):
        first_channel = sorted(context.approved_context.authorized_channels)[0]
    else:
        first_channel = sorted(
            context.approved_context.channel_policies,
            key=lambda policy: policy.channel.value,
        )[0].channel
    return SetterModelOutput(
        action=preflight.action,
        rationale=preflight.rationale,
        stage=context.workflow_stage.value,
        channel=first_channel,
        evidence_ids=[context.evidence_ids[0]],
        guardrail_ids=[],
        assertion_ids=[],
        message=None,
        cta=None,
        personalization_claims=None,
    )


def _validate_output(context: SetterContext, output: SetterModelOutput) -> None:
    if isinstance(context.approved_context, ApprovedCommercialSetterContext):
        allowed_channels = {
            channel for channel in context.approved_context.authorized_channels
            if channel in {"email", "phone"}
        }
        if output.guardrail_ids or output.assertion_ids:
            raise SetterOutputValidationError(
                "Commercial context output cannot reference workflow guardrails or assertions"
            )
    else:
        allowed_channels = {policy.channel for policy in context.approved_context.channel_policies}
    if output.stage != context.workflow_stage.value:
        raise SetterOutputValidationError("Output stage is not authorized")
    if output.channel not in allowed_channels:
        raise SetterOutputValidationError("Output channel is not authorized")

    allowed_evidence = set(context.evidence_ids)
    if not set(output.evidence_ids) <= allowed_evidence:
        raise SetterOutputValidationError("Output references unknown evidence")
    claim_evidence = {
        evidence_id
        for claim in output.personalization_claims or []
        for evidence_id in claim.evidence_ids
    }
    if not claim_evidence <= allowed_evidence or not claim_evidence <= set(output.evidence_ids):
        raise SetterOutputValidationError("Personalization claim evidence is not authorized")

    if not isinstance(context.approved_context, ApprovedCommercialSetterContext):
        allowed_guardrails = {item.code.value for item in context.approved_context.compiled_guardrails}
        if not set(output.guardrail_ids) <= allowed_guardrails:
            raise SetterOutputValidationError("Output references an unknown approved guardrail")
        allowed_assertions = {item.assertion_id for item in context.approved_context.reviewed_assertions}
        if not set(output.assertion_ids) <= allowed_assertions:
            raise SetterOutputValidationError("Output references an unknown approved assertion")


def _validate_supported_guardrails(
    context: SetterContext,
    output: SetterModelOutput,
) -> tuple[str, list[dict[str, str]]]:
    if output.action is not SetterDecisionAction.DRAFT_TOUCH_1:
        if output.action is SetterDecisionAction.NEEDS_HUMAN_REVIEW:
            return "review_required", [{
                "code": "setter_decision_requires_review",
                "message": "Setter could not authorize a Touch 1 draft.",
                "severity": "warning",
            }]
        return "passed", []

    if isinstance(context.approved_context, ApprovedCommercialSetterContext):
        if _contains_explicit_price(output.message or "", output.cta or ""):
            raise SetterOutputValidationError("Commercial context Touch 1 drafts must omit prices")
        return "review_required", [{
            "code": "commercial_contact_restrictions_require_review",
            "message": "Review the draft against approved natural-language contact restrictions before use.",
            "severity": "warning",
        }]

    applicable = context.approved_context.compiled_guardrails
    price_is_prohibited = any(
        item.code is PlaybookRuleCode.NO_PREMATURE_PRICING
        and item.effect is PolicyRuleEffect.MUST_NOT
        for item in applicable
    )
    if price_is_prohibited and _contains_explicit_price(output.message or "", output.cta or ""):
        raise SetterOutputValidationError("Draft violates the approved no-premature-pricing guardrail")

    findings: list[dict[str, str]] = []
    if price_is_prohibited:
        findings.append({
            "code": "price_guardrail_requires_human_review",
            "message": "Automated validation only detects explicit amount/currency expressions; review pricing semantics.",
            "severity": "warning",
        })

    unsupported_critical = [
        item for item in applicable
        if item.effect in {PolicyRuleEffect.MUST, PolicyRuleEffect.MUST_NOT}
        and item.code is not PlaybookRuleCode.NO_PREMATURE_PRICING
    ]
    if unsupported_critical:
        findings.append({
            "code": "critical_guardrail_requires_human_review",
            "message": "One or more approved critical natural-language guardrails require human review.",
            "severity": "warning",
        })
    if findings:
        return "review_required", findings
    return "passed", []


def _contains_explicit_price(*parts: str) -> bool:
    text = " ".join(parts)
    return any(pattern.search(text) for pattern in _PRICE_PATTERNS)


def _generation_metadata(audit: SetterGenerationAudit | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    return {
        "provider": audit.provider,
        "model": audit.model,
        "operation": audit.operation,
        "prompt_version": audit.prompt_version,
        "response_id": audit.response_id,
        "http_status": audit.http_status,
        "latency_ms": audit.latency_ms,
        "input_tokens": audit.input_tokens,
        "output_tokens": audit.output_tokens,
        "total_tokens": audit.total_tokens,
        "finish_reason": audit.finish_reason,
        "schema_validated": audit.schema_validated,
    }


__all__ = ["SetterPreconditionError", "SetterService"]
