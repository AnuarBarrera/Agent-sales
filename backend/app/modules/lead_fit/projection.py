"""Deterministic typed bridge from a current Lead Fit result to Setter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ai.contracts import (
    ProspectHypothesis,
    ProspectIntelligence,
    ProspectObservation,
    QualificationDecision,
    QualificationStatus,
)
from app.modules.lead_fit.contracts import (
    AssessmentResult,
    CriterionKind,
    FitClassification,
    FitEvaluationResult,
    FitPolicyContent,
    LeadDossier,
    canonical_sha256,
)
from app.modules.lead_fit.dossier import DossierError, load_server_dossier
from app.modules.lead_fit.models import (
    LeadFitEvaluation,
    LeadFitEvaluationStatus,
    LeadFitPolicy,
    LeadFitResearchGrant,
    LeadFitResearchGrantStatus,
)
from app.modules.lead_fit.questions import fresh_dossier
from app.modules.lead_fit.service import (
    LeadFitPolicyService,
    PolicyAuthorityNotFound,
    PolicyAuthorityStale,
)
from app.modules.prospect_analysis.models import ProspectAnalysis, ProspectAnalysisStatus


class FitProjectionError(ValueError):
    """The persisted evaluation cannot safely produce a Setter projection."""


@dataclass(frozen=True)
class VerifiedFitSnapshot:
    evaluation: LeadFitEvaluation
    policy: LeadFitPolicy
    dossier: LeadDossier
    result: FitEvaluationResult
    channels: tuple[str, ...]


def _status(value: object) -> str | None:
    return getattr(value, "value", value) if value is not None else None


def _analysis_for_evaluation(db, workspace_id: UUID, evaluation_id: UUID):
    return db.execute(select(ProspectAnalysis).where(
        ProspectAnalysis.workspace_id == workspace_id,
        ProspectAnalysis.fit_evaluation_id == evaluation_id,
    ).limit(1)).scalar_one_or_none()


def _verified_result_contract(evaluation: LeadFitEvaluation) -> FitEvaluationResult:
    result = evaluation.result
    if not isinstance(result, dict) or not isinstance(evaluation.result_digest, str):
        raise FitProjectionError("The latest fit evaluation has no verified result")
    if canonical_sha256(result) != evaluation.result_digest:
        raise FitProjectionError("The fit evaluation result digest is invalid")
    if not isinstance(result.get("identity_assessments", []), list):
        raise FitProjectionError("Fit identity assessments are invalid")
    # The durable result intentionally stores the decision payload, while its
    # status and contract envelope live in evaluation columns/audit metadata.
    contract_data = {key: value for key, value in result.items() if key != "identity_assessments"}
    contract_data.setdefault("status", "completed")
    contract_data.setdefault("contract_version", "lead-fit-1.0")
    try:
        contract = FitEvaluationResult.model_validate(contract_data)
    except (TypeError, ValueError) as exc:
        raise FitProjectionError("The persisted fit result does not match its contract") from exc
    if (
        contract.status.value != "completed"
        or contract.classification is None
        or _status(evaluation.classification) != contract.classification.value
        or evaluation.fit_score != contract.fit_score
        or evaluation.coverage != contract.coverage
        or evaluation.policy_digest != contract.policy_digest
        or evaluation.source_digest != contract.source_digest
        or evaluation.question_digest != contract.question_digest
        or evaluation.engine_version != contract.engine_version
    ):
        raise FitProjectionError("Persisted fit columns and result contract disagree")
    return contract


def _result_contract(evaluation: LeadFitEvaluation) -> FitEvaluationResult:
    """Strict compatibility wrapper for callers that require qualified fit."""
    contract = _verified_result_contract(evaluation)
    if contract.classification is None or contract.classification.value != "qualified":
        raise FitProjectionError("The latest Lead Fit evaluation is not qualified")
    return contract


def _qualified_channels(dossier: LeadDossier, allowed_channels: tuple[str, ...]) -> tuple[str, ...]:
    matched_ids = {item.evidence_id for item in dossier.sources if item.subject_status == "matched"}
    available = {
        contact.channel for contact in dossier.contacts
        if contact.channel in {"email", "phone"}
        and contact.channel in allowed_channels
        and contact.valid_format
        and contact.subject_status == "matched"
        and contact.evidence_ids
        and set(contact.evidence_ids) <= matched_ids
    }
    return tuple(channel for channel in ("email", "phone") if channel in available)


def _typed_projection(*, evaluation, policy, dossier, authorized_channels):
    result = _result_contract(evaluation)
    try:
        content = FitPolicyContent.model_validate(policy.content)
    except (TypeError, ValueError) as exc:
        raise FitProjectionError("The current fit policy is invalid") from exc
    criteria = {criterion.id: criterion for criterion in content.criteria}
    assessment_ids = [item.criterion_id for item in result.assessments]
    if len(assessment_ids) != len(set(assessment_ids)) or set(assessment_ids) != set(criteria):
        raise FitProjectionError("Fit result does not contain one assessment per current criterion")
    matched_ids = {source.evidence_id for source in dossier.sources if source.subject_status == "matched"}
    observations, hypotheses, missing = [], [], []
    references, confidences, positive, negative = [], [], [], []
    for assessment in result.assessments:
        criterion = criteria.get(assessment.criterion_id)
        if criterion is None:
            raise FitProjectionError("Fit result references a criterion outside the current policy")
        state = assessment.result
        if not assessment.evidence_ids:
            if (
                state is AssessmentResult.NOT_ESTABLISHED
                and criterion.kind is not CriterionKind.REQUIRED
                and not criterion.must_resolve
            ):
                missing.append(f"Criterio de encaje sin resolver: {criterion.description}")
                continue
            raise FitProjectionError("Fit result claim is missing attributable evidence")
        if (
            len(assessment.evidence_ids) != len(set(assessment.evidence_ids))
            or not set(assessment.evidence_ids) <= matched_ids
        ):
            raise FitProjectionError("Fit result references missing or unattributed evidence")
        references.extend(assessment.evidence_ids)
        confidences.append(assessment.confidence)
        label = {
            AssessmentResult.MATCHES: "respaldado",
            AssessmentResult.DOES_NOT_MATCH: "no respaldado",
            AssessmentResult.NOT_ESTABLISHED: "no establecido",
            AssessmentResult.CONFLICTING: "en conflicto",
        }[state]
        observations.append(ProspectObservation(
            statement=f"La evaluación registró el criterio «{criterion.description}» como {label}.",
            evidence_ids=list(assessment.evidence_ids),
        ))
        if state is AssessmentResult.MATCHES:
            positive.append(f"Criterio de encaje respaldado: {criterion.description}")
        elif state is AssessmentResult.DOES_NOT_MATCH:
            negative.append(f"Criterio de encaje no respaldado: {criterion.description}")
        elif state in {AssessmentResult.NOT_ESTABLISHED, AssessmentResult.CONFLICTING}:
            missing.append(f"Criterio de encaje sin resolver: {criterion.description}")
            hypotheses.append(ProspectHypothesis(
                statement=f"Hace falta revisar si la evidencia permite resolver «{criterion.description}».",
                confidence=assessment.confidence,
                supporting_evidence_ids=list(assessment.evidence_ids),
            ))
    if not references or not observations:
        raise FitProjectionError("A qualified fit result must cite attributable criterion evidence")
    channels = _qualified_channels(dossier, authorized_channels)
    if not channels:
        raise FitProjectionError("No approved Setter channel has current attributable contact evidence")
    contact_ids = [
        evidence_id for contact in dossier.contacts
        if contact.channel in channels and contact.valid_format and contact.subject_status == "matched"
        for evidence_id in contact.evidence_ids
    ]
    references = list(dict.fromkeys([*references, *contact_ids]))
    intelligence = ProspectIntelligence(
        summary=(
            f"Evaluación de encaje calificada con índice {result.fit_score}/100 "
            f"y cobertura {result.coverage:g}%. Requiere revisión humana antes de cualquier contacto."
        ),
        observations=observations, hypotheses=hypotheses, missing_information=missing[:20],
    )
    qualification = QualificationDecision(
        decision=QualificationStatus.QUALIFIED, score=result.fit_score,
        confidence=min(confidences), positive_signals=positive[:10], negative_signals=negative[:10],
        recommended_angle="Revisar el encaje documentado antes de preparar un borrador.",
        rationale=(
            "La política aprobada clasificó el expediente como qualified. El score es un índice de encaje; "
            "no expresa intención de compra ni autoriza contacto automático."
        ),
        evidence_ids=references,
    )
    return (
        ProspectIntelligence.model_validate(intelligence.model_dump(mode="python")),
        QualificationDecision.model_validate(qualification.model_dump(mode="python")),
        channels, tuple(references),
    )


def project_fit_analysis(
    *, db, evaluation: LeadFitEvaluation, policy: LeadFitPolicy, dossier: LeadDossier,
    workspace_id: UUID, lead_id: UUID, playbook_id: UUID, authorized_channels: tuple[str, ...],
) -> ProspectAnalysis:
    """Create or reuse one mapped projection for this exact current evaluation."""
    if evaluation.status is not LeadFitEvaluationStatus.COMPLETED:
        raise FitProjectionError("Only the latest completed evaluation can project to Setter")
    if (
        evaluation.workspace_id != workspace_id or evaluation.lead_id != lead_id
        or evaluation.policy_id != policy.id or policy.workspace_id != workspace_id
        or policy.playbook_id != playbook_id or dossier.workspace_id != workspace_id
        or dossier.lead_id != lead_id or evaluation.research_id != dossier.research_id
        or evaluation.policy_digest != policy.policy_digest
        or evaluation.source_digest != dossier.source_snapshot_digest
    ):
        raise FitProjectionError("Fit evaluation, policy, dossier, or playbook authority changed")
    intelligence, qualification, _channels, evidence_ids = _typed_projection(
        evaluation=evaluation, policy=policy, dossier=dossier,
        authorized_channels=authorized_channels,
    )
    existing = _analysis_for_evaluation(db, workspace_id, evaluation.id)
    if existing is not None:
        if (
            existing.sales_playbook_id != playbook_id
            or existing.research_id != dossier.research_id
            or existing.evidence_ids != list(evidence_ids)
            or existing.intelligence != intelligence.model_dump(mode="json")
            or existing.qualification != qualification.model_dump(mode="json")
        ):
            raise FitProjectionError("Stored fit projection does not match current verified authority")
        return existing
    analysis = ProspectAnalysis(
        id=uuid5(NAMESPACE_URL, f"lead-fit-setter:{workspace_id}:{evaluation.id}"),
        workspace_id=workspace_id, lead_id=lead_id, sales_playbook_id=playbook_id,
        research_id=dossier.research_id, fit_evaluation_id=evaluation.id,
        previous_analysis_id=None, status=ProspectAnalysisStatus.COMPLETED,
        evidence_ids=list(evidence_ids), intelligence=intelligence.model_dump(mode="json"),
        qualification=qualification.model_dump(mode="json"),
        intelligence_provider="lead-fit-1.0", intelligence_model=None,
        intelligence_prompt_version="lead-fit-1.0",
        intelligence_contract_version=intelligence.contract_version,
        intelligence_latency_ms=None, intelligence_tokens=None,
        qualification_provider="lead-fit-1.0", qualification_model=None,
        qualification_prompt_version="lead-fit-1.0",
        qualification_contract_version=qualification.contract_version,
        qualification_latency_ms=None, qualification_tokens=None,
        generated_at=evaluation.completed_at or evaluation.requested_at or datetime.now(UTC),
    )
    try:
        with db.begin_nested():
            db.add(analysis)
            db.flush()
    except IntegrityError:
        winner = _analysis_for_evaluation(db, workspace_id, evaluation.id)
        if winner is not None:
            return winner
        raise
    return analysis


def project_discovery_analysis(
    *, db, snapshot: VerifiedFitSnapshot, workspace_id: UUID, lead_id: UUID, playbook_id: UUID,
) -> ProspectAnalysis:
    """Persist an identity/contact projection while retaining unresolved fit honestly."""
    evaluation, policy, dossier, result = (
        snapshot.evaluation, snapshot.policy, snapshot.dossier, snapshot.result,
    )
    if result.classification is not FitClassification.NEEDS_MORE_RESEARCH:
        raise FitProjectionError("Discovery projection requires unresolved fit")
    if (
        evaluation.status is not LeadFitEvaluationStatus.COMPLETED
        or evaluation.workspace_id != workspace_id or evaluation.lead_id != lead_id
        or evaluation.policy_id != policy.id or policy.workspace_id != workspace_id
        or policy.playbook_id != playbook_id or dossier.workspace_id != workspace_id
        or dossier.lead_id != lead_id or evaluation.research_id != dossier.research_id
        or evaluation.policy_digest != policy.policy_digest
        or evaluation.source_digest != dossier.source_snapshot_digest
        or not snapshot.channels
    ):
        raise FitProjectionError("Discovery snapshot authority changed or is incomplete")
    matched = [source for source in dossier.sources if source.subject_status == "matched"]
    if not matched:
        raise FitProjectionError("Discovery requires attributed business identity evidence")
    contact_ids = [
        evidence_id for contact in dossier.contacts
        if contact.channel in snapshot.channels and contact.valid_format
        and contact.subject_status == "matched"
        and set(contact.evidence_ids) <= {source.evidence_id for source in matched}
        for evidence_id in contact.evidence_ids
    ]
    evidence_ids = list(dict.fromkeys([source.evidence_id for source in matched] + contact_ids))[:20]
    if not contact_ids or not evidence_ids:
        raise FitProjectionError("Discovery requires attributable contact evidence")
    intelligence = ProspectIntelligence(
        summary=(
            "La identidad del negocio y un canal de contacto están respaldados por evidencia actual. "
            "La oportunidad comercial todavía no está establecida; requiere una pregunta y revisión humana."
        ),
        observations=[ProspectObservation(
            statement="La fuente actual está atribuida al negocio identificado.",
            evidence_ids=[source.evidence_id for source in matched[:20]],
        )],
        hypotheses=[],
        missing_information=[
            f"Oportunidad sin establecer: {criterion.description}"
            for criterion in FitPolicyContent.model_validate(policy.content).criteria
            if criterion.kind is CriterionKind.PREFERENCE
            and any(
                assessment.criterion_id == criterion.id
                and assessment.result in {AssessmentResult.NOT_ESTABLISHED, AssessmentResult.CONFLICTING}
                for assessment in result.assessments
            )
        ][:20],
    )
    qualification = QualificationDecision(
        decision=QualificationStatus.NEEDS_MORE_RESEARCH,
        score=result.fit_score,
        confidence=0.0,
        positive_signals=[], negative_signals=[],
        recommended_angle="Preguntar cómo gestiona actualmente el negocio el tema de la oferta aprobada.",
        rationale=(
            "La evidencia disponible no establece la oportunidad. Este estado conserva la clasificación "
            "original; confidence 0.0 indica que no hay conclusión de oportunidad, no una probabilidad."
        ),
        evidence_ids=evidence_ids,
    )
    existing = _analysis_for_evaluation(db, workspace_id, evaluation.id)
    intelligence_json = intelligence.model_dump(mode="json")
    qualification_json = qualification.model_dump(mode="json")
    if existing is not None:
        if (
            existing.sales_playbook_id != playbook_id or existing.research_id != dossier.research_id
            or existing.evidence_ids != evidence_ids or existing.intelligence != intelligence_json
            or existing.qualification != qualification_json
        ):
            raise FitProjectionError("Stored fit projection does not match current verified authority")
        return existing
    analysis = ProspectAnalysis(
        id=uuid5(NAMESPACE_URL, f"lead-fit-setter:{workspace_id}:{evaluation.id}"),
        workspace_id=workspace_id, lead_id=lead_id, sales_playbook_id=playbook_id,
        research_id=dossier.research_id, fit_evaluation_id=evaluation.id,
        previous_analysis_id=None, status=ProspectAnalysisStatus.COMPLETED,
        evidence_ids=evidence_ids, intelligence=intelligence_json,
        qualification=qualification_json, intelligence_provider="lead-fit-1.0",
        intelligence_model=None, intelligence_prompt_version="lead-fit-1.0",
        intelligence_contract_version=intelligence.contract_version, intelligence_latency_ms=None,
        intelligence_tokens=None, qualification_provider="lead-fit-1.0",
        qualification_model=None, qualification_prompt_version="lead-fit-1.0",
        qualification_contract_version=qualification.contract_version,
        qualification_latency_ms=None, qualification_tokens=None,
        generated_at=evaluation.completed_at or evaluation.requested_at or datetime.now(UTC),
    )
    try:
        with db.begin_nested():
            db.add(analysis)
            db.flush()
    except IntegrityError:
        winner = _analysis_for_evaluation(db, workspace_id, evaluation.id)
        if winner is not None:
            return winner
        raise
    return analysis


def _rebind_persisted_identity(
    dossier: LeadDossier, result: dict, *, entity_confidence_floor: float,
) -> LeadDossier:
    """Apply result-digest-protected Jev identity decisions through the shared binder."""
    from app.modules.lead_fit.contracts import AssessmentResult, SourceAssessment
    from app.modules.lead_fit.evidence import bind_evidence_subjects

    rows = result.get("identity_assessments", [])
    if not isinstance(rows, list):
        raise FitProjectionError("Fit identity assessments are invalid")
    available = {source.evidence_id for source in dossier.sources}
    seen: set[str] = set()
    answers = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FitProjectionError("Fit identity assessment is malformed")
        evidence_id = row.get("evidence_id")
        if not isinstance(evidence_id, str) or evidence_id not in available or evidence_id in seen:
            raise FitProjectionError("Fit identity assessment references foreign or duplicate evidence")
        seen.add(evidence_id)
        try:
            status = AssessmentResult(row.get("status"))
            answer = SourceAssessment(
                criterion_id="__entity__",
                evidence_id=evidence_id,
                status=status,
                method=row.get("method"),
                confidence=row.get("confidence"),
                probabilities={},
            )
        except (TypeError, ValueError) as exc:
            raise FitProjectionError("Fit identity assessment does not match its contract") from exc
        answers[f"entity_{evidence_id}"] = answer
    return bind_evidence_subjects(
        dossier,
        answers,
        entity_confidence_floor=entity_confidence_floor,
    )


def load_current_fit_snapshot(
    db, *, workspace_id: UUID, lead_id: UUID, playbook_id: UUID,
    authorized_channels: tuple[str, ...],
) -> VerifiedFitSnapshot:
    """Load and verify current fit authority without creating a Setter projection."""
    from app.modules.lead_fit.evaluation_service import LeadFitEvaluationService

    active_research = db.execute(
        select(LeadFitResearchGrant).where(
            LeadFitResearchGrant.workspace_id == workspace_id,
            LeadFitResearchGrant.lead_id == lead_id,
            LeadFitResearchGrant.status.in_(
                (LeadFitResearchGrantStatus.AUTHORIZED, LeadFitResearchGrantStatus.RUNNING)
            ),
        ).limit(1)
    ).scalar_one_or_none()
    if active_research is not None:
        raise FitProjectionError("Incremental research is authorized or running for this lead")

    evaluation = LeadFitEvaluationService(db).latest(workspace_id, lead_id)
    if evaluation is None:
        raise FitProjectionError("A current Lead Fit evaluation is required")
    require_current_fit_evaluation(
        workspace_id=workspace_id, lead_id=lead_id, evaluation=evaluation,
    )
    try:
        policy = LeadFitPolicyService(db).current(workspace_id, playbook_id)
    except (PolicyAuthorityNotFound, PolicyAuthorityStale) as exc:
        raise FitProjectionError("The approved commercial authority is unavailable") from exc
    if policy is None or policy.id != evaluation.policy_id:
        raise FitProjectionError("The latest Lead Fit policy is missing or stale")
    result = _verified_result_contract(evaluation)
    try:
        dossier = load_server_dossier(
            db, workspace_id=workspace_id, lead_id=lead_id, policy=policy,
        )
    except DossierError as exc:
        raise FitProjectionError("The current source dossier is invalid") from exc
    if dossier.source_snapshot_digest != evaluation.source_digest:
        raise FitProjectionError("The latest Lead Fit source snapshot is stale")
    try:
        content = FitPolicyContent.model_validate(policy.content)
        dossier = _rebind_persisted_identity(
            dossier,
            evaluation.result,
            entity_confidence_floor=content.entity_confidence_floor,
        )
        dossier = fresh_dossier(content, dossier)
    except (TypeError, ValueError) as exc:
        raise FitProjectionError("The current policy or evidence freshness is invalid") from exc
    channels = _qualified_channels(dossier, authorized_channels)
    return VerifiedFitSnapshot(
        evaluation=evaluation, policy=policy, dossier=dossier, result=result, channels=channels,
    )


def project_latest_fit_for_setter(
    db, *, workspace_id: UUID, lead_id: UUID, playbook_id: UUID,
    authorized_channels: tuple[str, ...],
) -> tuple[ProspectAnalysis, LeadFitEvaluation, LeadFitPolicy, LeadDossier, tuple[str, ...]]:
    """Revalidate current authority and project only qualified fit into Setter."""
    snapshot = load_current_fit_snapshot(
        db, workspace_id=workspace_id, lead_id=lead_id, playbook_id=playbook_id,
        authorized_channels=authorized_channels,
    )
    if snapshot.result.classification is None or snapshot.result.classification.value != "qualified":
        raise FitProjectionError("The latest Lead Fit evaluation is not qualified")
    analysis = project_fit_analysis(
        db=db, evaluation=snapshot.evaluation, policy=snapshot.policy, dossier=snapshot.dossier,
        workspace_id=workspace_id, lead_id=lead_id, playbook_id=playbook_id,
        authorized_channels=authorized_channels,
    )
    return analysis, snapshot.evaluation, snapshot.policy, snapshot.dossier, snapshot.channels


def require_current_fit_evaluation(*, workspace_id: UUID, lead_id: UUID, evaluation: LeadFitEvaluation):
    if (
        evaluation.status is not LeadFitEvaluationStatus.COMPLETED
        or evaluation.workspace_id != workspace_id or evaluation.lead_id != lead_id
    ):
        raise FitProjectionError("The latest fit evaluation is not current and completed")
    return evaluation


def is_fit_evaluation_eligible_for_setter(
    evaluation: LeadFitEvaluation, allowed_channels: tuple[str, ...] = (),
    *, policy: LeadFitPolicy | None = None, dossier: LeadDossier | None = None,
) -> bool:
    if evaluation.status is not LeadFitEvaluationStatus.COMPLETED or _status(evaluation.classification) != "qualified":
        return False
    if policy is None or dossier is None:
        return False
    try:
        _typed_projection(
            evaluation=evaluation, policy=policy, dossier=dossier,
            authorized_channels=allowed_channels,
        )
    except (FitProjectionError, TypeError, ValueError):
        return False
    return True
