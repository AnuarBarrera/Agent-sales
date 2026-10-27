"""Trusted, tenant-scoped input assembled from persisted prospect authority."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import ProspectIntelligence, QualificationDecision
from app.modules.leads.models import Business, Lead
from app.modules.prospect_analysis.models import ProspectAnalysis, ProspectAnalysisStatus
from app.modules.prospect_research.models import (
    ProspectResearch,
    ProspectResearchStatus,
    ResearchEvidence,
)
from app.modules.sales_playbook.commercial_context import (
    ApprovedCommercialContext,
    verified_approved_commercial_context,
)
from app.modules.sales_playbook.commercial_service import CommercialContextService
from app.modules.sales_playbook.models import SalesPlaybook, SalesPlaybookStatus
from app.modules.sales_playbook.schemas import WorkflowStageCode
from app.modules.sales_playbook.service import (
    ApprovedSetterContext,
    approved_setter_context,
    verified_approved_semantic_playbook,
)
from app.modules.seller_profile.models import SellerProfile
from app.modules.setter.schemas import SetterDecisionAction


class SetterPreconditionError(ValueError):
    """A prospect is not currently eligible for Setter execution."""


class SetterEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    operation: str
    source_type: str
    source_url: str
    source_title: str | None
    excerpt: str
    retrieved_at: datetime


class SetterBusinessContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    address: str | None
    phone: str | None
    website: str | None
    rating: float | None
    reviews_total: int | None
    giro: str | None
    zona: str | None


class SetterSellerProfileContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    business_name: str
    description: str | None
    offer_summary: str | None
    target_customer_summary: str | None
    location_or_market: str | None
    price_context: str | None
    primary_goal: str | None
    differentiators: str | None
    sales_constraints: str | None


class ApprovedCommercialSetterContext(BaseModel):
    """Commercial v4 authority for reviewed drafting, with no workflow graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["4.0"] = "4.0"
    commercial_context: ApprovedCommercialContext
    # WhatsApp remains representable in legacy snapshots; current v4 draft
    # construction filters it before this authority projection is built.
    authorized_channels: tuple[Literal["email", "phone", "whatsapp"], ...]
    snapshot_digest: str


class SetterContext(BaseModel):
    """Only server-loaded evidence, qualification, and verified approved authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    lead_id: UUID
    analysis_id: UUID
    research_id: UUID
    playbook_id: UUID
    playbook_version: str
    playbook_digest: str
    fit_evaluation_id: UUID | None = None
    fit_policy_id: UUID | None = None
    fit_policy_digest: str | None = None
    fit_source_digest: str | None = None
    fit_result_digest: str | None = None
    draft_purpose: Literal["discovery", "opportunity_grounded"] | None = None
    workflow_stage: WorkflowStageCode = WorkflowStageCode.TOUCH_1
    evidence_ids: tuple[str, ...] = Field(..., max_length=20)
    evidence: tuple[SetterEvidence, ...] = Field(..., max_length=20)
    intelligence: ProspectIntelligence
    qualification: QualificationDecision
    business: SetterBusinessContext
    seller_profile: SetterSellerProfileContext
    approved_context: ApprovedSetterContext | ApprovedCommercialSetterContext


class SetterPreflightResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: SetterDecisionAction
    rationale: str
    context: SetterContext


def _fail(message: str) -> SetterPreconditionError:
    return SetterPreconditionError(message)


def build_setter_context(db: Session, lead_id: UUID, workspace_id: UUID) -> SetterContext:
    """Build the exact trusted input snapshot; reject stale or cross-tenant pieces."""
    lead = db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if lead is None:
        raise _fail("Lead not found in this workspace")
    if lead.contacted or lead.first_contacted_at is not None:
        raise _fail("Touch 1 is unavailable after first contact")

    business = db.execute(
        select(Business).where(
            Business.id == lead.business_id,
            Business.workspace_id == workspace_id,
        ).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if business is None:
        raise _fail("Lead business is not available in this workspace")

    profile = db.execute(
        select(SellerProfile).where(SellerProfile.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if profile is None:
        raise _fail("Seller profile is required")

    playbook = db.execute(
        select(SalesPlaybook).where(
            SalesPlaybook.workspace_id == workspace_id,
            SalesPlaybook.status == SalesPlaybookStatus.APPROVED,
        ).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if playbook is None:
        raise _fail("An approved SalesPlaybook is required")
    try:
        if playbook.content_contract_version == "4.0":
            commercial_authority = verified_approved_commercial_context(playbook)
            CommercialContextService(db).validate_commercial_bindings(
                workspace_id,
                commercial_authority.source_snapshot,
                playbook.source_payload,
            )
            authority: ApprovedSetterContext | ApprovedCommercialSetterContext = ApprovedCommercialSetterContext(
                commercial_context=commercial_authority,
                authorized_channels=tuple(
                    channel for channel in commercial_authority.communication.allowed_channels
                    if channel in {"email", "phone"}
                ),
                snapshot_digest=commercial_authority.content_digest,
            )
            snapshot_digest = commercial_authority.content_digest
            if not authority.authorized_channels:
                raise _fail("Approved commercial context has no authorized channel")
        else:
            _, snapshot_digest = verified_approved_semantic_playbook(playbook)
            authority = approved_setter_context(playbook)
            if authority.contract_version != "3.0":
                raise _fail("Setter requires an approved SalesPlaybook v3 or v4 snapshot")
            if not any(stage.code is WorkflowStageCode.TOUCH_1 for stage in authority.workflow.stages):
                raise _fail("Approved workflow does not authorize Touch 1")
            if not authority.channel_policies:
                raise _fail("Approved playbook has no authorized communication channel")
    except SetterPreconditionError:
        raise
    except (TypeError, ValueError) as exc:
        raise _fail("The approved SalesPlaybook snapshot or sources are invalid") from exc

    from app.core.config import get_settings

    fit_evaluation_id = None
    fit_policy_id = None
    fit_policy_digest = None
    fit_source_digest = None
    fit_result_digest = None
    fit_dossier = None
    fit_channels: tuple[str, ...] = ()
    if get_settings().lead_fit_enabled:
        if playbook.content_contract_version != "4.0" or not isinstance(
            authority, ApprovedCommercialSetterContext
        ):
            raise _fail("Fit-aware Setter requires a current approved commercial context")
        try:
            from app.modules.lead_fit.projection import (
                FitProjectionError,
                load_current_fit_snapshot,
                project_discovery_analysis,
                project_fit_analysis,
            )
            from app.modules.setter.draft_readiness import evaluate_draft_readiness

            snapshot = load_current_fit_snapshot(
                db, workspace_id=workspace_id, lead_id=lead_id, playbook_id=playbook.id,
                authorized_channels=authority.authorized_channels,
            )
            readiness = evaluate_draft_readiness(
                policy=snapshot.policy.content, result=snapshot.result, dossier=snapshot.dossier,
                authorized_channels=authority.authorized_channels,
            )
            if not readiness.draft_ready:
                raise FitProjectionError(f"Draft readiness blocked: {readiness.reason_codes[0]}")
            fit_evaluation, fit_policy, fit_dossier = (
                snapshot.evaluation, snapshot.policy, snapshot.dossier,
            )
            fit_channels = readiness.contact_channels
            if readiness.draft_purpose == "discovery":
                analysis = project_discovery_analysis(
                    db=db, snapshot=snapshot, workspace_id=workspace_id, lead_id=lead_id,
                    playbook_id=playbook.id,
                )
            else:
                analysis = project_fit_analysis(
                    db=db, evaluation=fit_evaluation, policy=fit_policy, dossier=fit_dossier,
                    workspace_id=workspace_id, lead_id=lead_id, playbook_id=playbook.id,
                    authorized_channels=authority.authorized_channels,
                )
        except (FitProjectionError, ValueError, TypeError) as exc:
            raise _fail("The latest Lead Fit evaluation is not eligible for Setter") from exc
        if not fit_channels:
            raise _fail("No approved Setter channel has current attributable contact evidence")
        authority = authority.model_copy(update={"authorized_channels": fit_channels})
        fit_evaluation_id = fit_evaluation.id
        fit_policy_id = fit_policy.id
        fit_policy_digest = fit_policy.policy_digest
        fit_source_digest = fit_evaluation.source_digest
        fit_result_digest = fit_evaluation.result_digest
        research = None
    else:
        from app.modules.lead_fit.evaluation_service import LeadFitEvaluationService

        if LeadFitEvaluationService(db).latest(workspace_id, lead_id) is not None:
            raise _fail("Lead Fit history requires the current fit-aware Setter mode")
        analysis = db.execute(
            select(ProspectAnalysis)
            .where(
                ProspectAnalysis.workspace_id == workspace_id,
                ProspectAnalysis.lead_id == lead_id,
                ProspectAnalysis.status == ProspectAnalysisStatus.COMPLETED,
            )
            .order_by(ProspectAnalysis.generated_at.desc().nullslast(), ProspectAnalysis.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if analysis is None:
            raise _fail("A successful prospect analysis is required")
        if analysis.fit_evaluation_id is not None:
            raise _fail("A fit-backed analysis requires the current fit-aware Setter mode")
        if analysis.sales_playbook_id != playbook.id:
            raise _fail("Prospect analysis references a different SalesPlaybook")

        research = db.execute(
            select(ProspectResearch)
            .where(
                ProspectResearch.workspace_id == workspace_id,
                ProspectResearch.lead_id == lead_id,
                ProspectResearch.status == ProspectResearchStatus.COMPLETED,
            )
            .order_by(ProspectResearch.completed_at.desc().nullslast(), ProspectResearch.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if research is None:
            raise _fail("Successful prospect research is required")
        if analysis.research_id != research.id:
            raise _fail("Prospect analysis does not use the latest research snapshot")

    if (
        not analysis.evidence_ids
        or len(analysis.evidence_ids) > 20
        or len(analysis.evidence_ids) != len(set(analysis.evidence_ids))
    ):
        raise _fail("Prospect analysis evidence references are empty or duplicated")
    if fit_dossier is not None:
        sources_by_id = {item.evidence_id: item for item in fit_dossier.sources}
        if not set(analysis.evidence_ids) <= set(sources_by_id):
            raise _fail("Fit projection references evidence outside the current dossier")
        evidence_rows = [sources_by_id[item_id] for item_id in analysis.evidence_ids]
        evidence_by_id = {item.evidence_id: item for item in evidence_rows}
    else:
        evidence_rows = db.execute(
            select(ResearchEvidence)
            .where(
                ResearchEvidence.workspace_id == workspace_id,
                ResearchEvidence.lead_id == lead_id,
                ResearchEvidence.research_id == research.id,
                ResearchEvidence.evidence_id.in_(analysis.evidence_ids),
            )
            .order_by(ResearchEvidence.retrieved_at, ResearchEvidence.id)
            .execution_options(populate_existing=True)
        ).scalars().all()
        evidence_by_id = {item.evidence_id: item for item in evidence_rows}
        if set(evidence_by_id) != set(analysis.evidence_ids):
            raise _fail("Analysis evidence is not present on its exact research snapshot")

    try:
        intelligence = ProspectIntelligence.model_validate(analysis.intelligence)
        qualification = QualificationDecision.model_validate(analysis.qualification)
    except (TypeError, ValueError) as exc:
        raise _fail("Prospect intelligence or qualification is invalid") from exc
    if not set(intelligence_evidence_ids(intelligence)) <= set(evidence_by_id):
        raise _fail("Prospect intelligence references evidence outside the current context")
    if not set(qualification.evidence_ids) <= set(evidence_by_id):
        raise _fail("Qualification references evidence outside the current context")

    # Both APIs verify the persisted approval snapshot. They must resolve to
    # the same digest before the context is eligible for a provider request.
    if authority.snapshot_digest != snapshot_digest:
        raise _fail("Approved SalesPlaybook digest changed during context assembly")

    return SetterContext(
        workspace_id=workspace_id,
        lead_id=lead.id,
        analysis_id=analysis.id,
        research_id=fit_dossier.research_id if fit_dossier is not None else research.id,
        playbook_id=playbook.id,
        playbook_version=str(playbook.version),
        playbook_digest=snapshot_digest,
        fit_evaluation_id=fit_evaluation_id,
        fit_policy_id=fit_policy_id,
        fit_policy_digest=fit_policy_digest,
        fit_source_digest=fit_source_digest,
        fit_result_digest=fit_result_digest,
        draft_purpose=(
            readiness.draft_purpose if get_settings().lead_fit_enabled else None
        ),
        evidence_ids=tuple(analysis.evidence_ids),
        evidence=tuple(
            SetterEvidence(
                evidence_id=item.evidence_id,
                operation=item.type if fit_dossier is not None else item.operation,
                source_type=item.provider if fit_dossier is not None else item.source_type,
                source_url=item.source_url,
                source_title=None if fit_dossier is not None else item.source_title,
                excerpt=item.excerpt,
                retrieved_at=item.retrieved_at,
            )
            for item in evidence_rows
        ),
        intelligence=intelligence,
        qualification=qualification,
        business=SetterBusinessContext(
            name=business.name,
            address=business.address,
            phone=None if fit_dossier is not None else business.phone,
            website=None if fit_dossier is not None else business.website,
            rating=business.rating,
            reviews_total=business.reviews_total,
            giro=lead.giro,
            zona=lead.zona,
        ),
        seller_profile=SetterSellerProfileContext.model_validate(
            {
                "business_name": profile.business_name,
                "description": profile.description,
                "offer_summary": profile.offer_summary,
                "target_customer_summary": profile.target_customer_summary,
                "location_or_market": profile.location_or_market,
                "price_context": profile.price_context,
                "primary_goal": profile.primary_goal,
                "differentiators": profile.differentiators,
                "sales_constraints": profile.sales_constraints,
            }
        ),
        approved_context=authority,
    )


def intelligence_evidence_ids(intelligence: ProspectIntelligence) -> tuple[str, ...]:
    return tuple(
        evidence_id
        for item in intelligence.observations
        for evidence_id in item.evidence_ids
    ) + tuple(
        evidence_id
        for item in intelligence.hypotheses
        for evidence_id in item.supporting_evidence_ids
    )
