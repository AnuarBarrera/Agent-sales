"""Pure, fail-closed decision about which Setter draft can be prepared."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.lead_fit.contracts import (
    AssessmentResult,
    CriterionKind,
    FitClassification,
    FitEvaluationResult,
    FitPolicyContent,
    LeadDossier,
)

DraftPurpose = Literal["discovery", "opportunity_grounded"]
ReadinessReason = Literal[
    "authority_unavailable", "evaluation_unavailable", "evaluation_stale",
    "research_active", "identity_unresolved", "excluded", "required_unresolved",
    "required_failed", "contact_unavailable", "opportunity_unresolved", "qualified_evidence",
]


class DraftReadiness(BaseModel):
    """Immutable output of the draft-purpose decision table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_ready: bool
    draft_purpose: DraftPurpose | None = None
    reason_codes: tuple[ReadinessReason, ...] = Field(default_factory=tuple)
    identity_status: Literal["matched", "unresolved"]
    contact_channels: tuple[Literal["email", "phone"], ...] = Field(default_factory=tuple)

    @classmethod
    def blocked(
        cls,
        reason: ReadinessReason,
        *,
        identity_status: Literal["matched", "unresolved"] = "unresolved",
        contact_channels: tuple[Literal["email", "phone"], ...] = (),
    ) -> DraftReadiness:
        return cls(
            draft_ready=False, draft_purpose=None, reason_codes=(reason,),
            identity_status=identity_status, contact_channels=contact_channels,
        )


def evaluate_draft_readiness(
    *,
    policy: FitPolicyContent | dict,
    result: FitEvaluationResult | dict,
    dossier: LeadDossier,
    authorized_channels: tuple[str, ...],
) -> DraftReadiness:
    """Choose discovery only for honest unknown opportunity with current evidence."""
    try:
        policy = FitPolicyContent.model_validate(policy)
        if isinstance(result, dict):
            result = {key: value for key, value in result.items() if key != "identity_assessments"}
        result = FitEvaluationResult.model_validate(result)
    except (TypeError, ValueError):
        return DraftReadiness.blocked("evaluation_unavailable")

    matched_sources = {
        source.evidence_id for source in dossier.sources if source.subject_status == "matched"
    }
    if not matched_sources:
        return DraftReadiness.blocked("identity_unresolved")
    if result.status.value != "completed" or result.classification is None:
        return DraftReadiness.blocked("evaluation_unavailable", identity_status="matched")

    assessments = {item.criterion_id: item for item in result.assessments}
    criteria = {item.id: item for item in policy.criteria}
    if len(assessments) != len(result.assessments) or set(assessments) != set(criteria):
        return DraftReadiness.blocked("evaluation_stale", identity_status="matched")

    exclusion_match = any(
        criterion.kind is CriterionKind.EXCLUSION
        and assessments[criterion.id].result is AssessmentResult.MATCHES
        and assessments[criterion.id].evidence_ids
        and set(assessments[criterion.id].evidence_ids) <= matched_sources
        for criterion in policy.criteria
    )
    if exclusion_match or result.classification is FitClassification.NOT_QUALIFIED:
        return DraftReadiness.blocked("excluded", identity_status="matched")

    for criterion in policy.criteria:
        if criterion.kind is not CriterionKind.REQUIRED and not criterion.must_resolve:
            continue
        assessment = assessments[criterion.id]
        if assessment.result is AssessmentResult.DOES_NOT_MATCH:
            return DraftReadiness.blocked("required_failed", identity_status="matched")
        if assessment.result in {AssessmentResult.NOT_ESTABLISHED, AssessmentResult.CONFLICTING}:
            return DraftReadiness.blocked("required_unresolved", identity_status="matched")
        if not assessment.evidence_ids or not set(assessment.evidence_ids) <= matched_sources:
            return DraftReadiness.blocked("required_unresolved", identity_status="matched")

    channels_available = {
        contact.channel for contact in dossier.contacts
        if contact.channel in {"email", "phone"}
        and contact.channel in authorized_channels
        and contact.valid_format
        and contact.subject_status == "matched"
        and contact.evidence_ids
        and set(contact.evidence_ids) <= matched_sources
    }
    channels = tuple(channel for channel in ("email", "phone") if channel in channels_available)
    if not channels:
        return DraftReadiness.blocked(
            "contact_unavailable", identity_status="matched", contact_channels=channels,
        )

    if result.classification is FitClassification.QUALIFIED:
        # Positive claims on any criterion must be backed by attributable sources.
        if any(
            assessment.result is AssessmentResult.MATCHES
            and (not assessment.evidence_ids or not set(assessment.evidence_ids) <= matched_sources)
            for assessment in assessments.values()
        ):
            return DraftReadiness.blocked(
                "qualified_evidence", identity_status="matched", contact_channels=channels,
            )
        return DraftReadiness(
            draft_ready=True, draft_purpose="opportunity_grounded",
            reason_codes=("qualified_evidence",), identity_status="matched", contact_channels=channels,
        )

    if result.classification is FitClassification.NEEDS_MORE_RESEARCH and any(
        criterion.kind is CriterionKind.PREFERENCE
        and assessments[criterion.id].result in {
            AssessmentResult.NOT_ESTABLISHED, AssessmentResult.CONFLICTING,
        }
        for criterion in policy.criteria
    ):
        return DraftReadiness(
            draft_ready=True, draft_purpose="discovery",
            reason_codes=("opportunity_unresolved",), identity_status="matched",
            contact_channels=channels,
        )
    return DraftReadiness.blocked("evaluation_unavailable", identity_status="matched")
