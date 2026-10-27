"""Server-authoritative evaluation request and leased Jev execution."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.integrations.jev import DEFAULT_MODEL, JevClientError, JevDecisionClient
from app.modules.lead_fit.contracts import (
    AssessmentResult,
    CriterionEvaluator,
    FitPolicyContent,
    SourceAssessment,
    canonical_sha256,
)
from app.modules.lead_fit.dossier import (
    EVIDENCE_EXCERPT_MAX_CHARS,
    DossierError,
    load_server_dossier,
)
from app.modules.lead_fit.engine import aggregate_assessments, classify_fit
from app.modules.lead_fit.exact_rules import evaluate_exact_rule
from app.modules.lead_fit.models import (
    LeadFitEvaluation,
    LeadFitEvaluationStatus,
    LeadFitPolicy,
)
from app.modules.lead_fit.questions import (
    build_criterion_questions,
    build_entity_questions,
    fresh_dossier,
)
from app.modules.lead_fit.schemas import FitPolicyRead
from app.modules.lead_fit.service import (
    LeadFitPolicyService,
    PolicyAuthorityNotFound,
    PolicyAuthorityStale,
)
from app.modules.leads.models import Lead

ENGINE_VERSION = "lead-fit-1.0"


class EvaluationConflict(ValueError):
    """A requested policy/source/retry does not match current server state."""


class EvaluationUnavailable(RuntimeError):
    """The verified commercial authority cannot currently be loaded."""


class LeadFitEvaluationService:
    """Creates stable intents and executes at most two reserved Jev calls."""

    def __init__(
        self,
        db,
        policy_service: LeadFitPolicyService | None = None,
        *,
        jev_factory: Callable[[], object] | None = None,
        dossier_loader: Callable = load_server_dossier,
        requested_model: str = DEFAULT_MODEL,
    ) -> None:
        self.db = db
        self.policy_service = policy_service or LeadFitPolicyService(db)
        self._jev_factory = jev_factory or self._configured_jev
        self._dossier_loader = dossier_loader
        self.requested_model = requested_model
        self._authorized_queue_reenqueue: set[UUID] = set()

    def _configured_jev(self):
        # Provider settings/client creation are deliberately deferred until dispatch.
        from app.core.config import get_settings

        settings = get_settings()
        return JevDecisionClient(
            api_key=settings.openrouter_api_key,
            model=self.requested_model,
            timeout_seconds=settings.jev_timeout_seconds,
            max_request_bytes=settings.jev_max_request_bytes,
            authorized_models=settings.jev_authorized_models,
            enabled=settings.jev_enabled,
        )

    def context(self, *, workspace_id: UUID, lead_id: UUID, playbook_id: UUID) -> dict:
        """Return verified policy/source digests for UI preview without providers."""
        policy = self._current_policy(workspace_id, playbook_id)
        if policy is None:
            raise EvaluationConflict("approved policy is missing or stale")
        dossier = self._dossier_loader(
            self.db, workspace_id=workspace_id, lead_id=lead_id, policy=policy,
        )
        # Snapshot aditivo de la misma política verificada (sin nueva aprobación,
        # sin segunda lectura y sin efectos de escritura). Si el snapshot no puede
        # serializarse, el contexto sigue sirviendo los campos anteriores y la
        # ficha detallada queda como "no disponible" en lugar de romper la lectura.
        try:
            policy_snapshot: FitPolicyRead | None = FitPolicyRead.model_validate(policy)
        except ValidationError:
            policy_snapshot = None
        return {
            "policy_id": policy.id,
            "policy_digest": policy.policy_digest,
            "expected_source_digest": dossier.source_snapshot_digest,
            "research_id": dossier.research_id,
            "policy": policy_snapshot,
        }

    def request(
        self,
        *,
        workspace_id: UUID,
        lead_id: UUID,
        user_id: UUID,
        policy_id: UUID,
        expected_policy_digest: str,
        expected_source_digest: str,
        idempotency_key: str,
        retry_of: UUID | None = None,
        reenqueue: bool = False,
    ) -> LeadFitEvaluation:
        """Validate current authority and persist/reuse a complete canonical intent."""
        if not idempotency_key or len(idempotency_key) > 128:
            raise EvaluationConflict("invalid idempotency key")
        lead = self.db.execute(select(Lead).where(
            Lead.workspace_id == workspace_id,
            Lead.id == lead_id,
        ).with_for_update(of=Lead)).scalar_one_or_none()
        if lead is None:
            raise EvaluationConflict("lead not found")
        policy = self.db.execute(select(LeadFitPolicy).where(
            LeadFitPolicy.workspace_id == workspace_id,
            LeadFitPolicy.id == policy_id,
        )).scalar_one_or_none()
        if policy is None:
            raise EvaluationConflict("approved policy is missing")
        current_policy = self._current_policy(workspace_id, policy.playbook_id)
        if (
            current_policy is None or current_policy.id != policy.id
            or current_policy.policy_digest != expected_policy_digest
            or policy.policy_digest != expected_policy_digest
        ):
            raise EvaluationConflict("approved policy changed; refresh evaluation context")
        try:
            dossier = self._dossier_loader(
                self.db, workspace_id=workspace_id, lead_id=lead_id, policy=policy,
            )
        except DossierError as exc:
            raise EvaluationConflict("lead source dossier is unavailable") from exc
        if dossier.source_snapshot_digest != expected_source_digest:
            raise EvaluationConflict("lead sources changed; refresh evaluation context")

        content = FitPolicyContent.model_validate(policy.content)
        question_digest = _question_plan_digest(content, dossier)
        intent_key = hashlib.sha256(json.dumps({
            "action_key": idempotency_key,
            "workspace_id": str(workspace_id),
            "lead_id": str(lead_id),
            "policy_digest": policy.policy_digest,
            "source_digest": dossier.source_snapshot_digest,
            "engine_version": ENGINE_VERSION,
            "question_digest": question_digest,
            "requested_model": self.requested_model,
        }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

        existing = self.db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.idempotency_key == intent_key,
        ).with_for_update()).scalar_one_or_none()
        if existing is not None:
            if (existing.lead_id != lead_id or existing.policy_digest != policy.policy_digest
                    or existing.source_digest != dossier.source_snapshot_digest):
                raise EvaluationConflict("idempotency key is bound to different evaluation inputs")
            reenqueue_eligible = (
                existing.call_count == 0
                and existing.dispatched_at is None
                and (
                    existing.status is LeadFitEvaluationStatus.PENDING
                    or existing.status is LeadFitEvaluationStatus.FAILED
                    and existing.error_code == "enqueue_failed"
                )
            )
            if reenqueue and reenqueue_eligible:
                latest_existing = self._latest_attempt(workspace_id, lead_id)
                if (latest_existing is not None and latest_existing.id != existing.id
                        and (latest_existing.status in {
                            LeadFitEvaluationStatus.PENDING, LeadFitEvaluationStatus.RUNNING,
                        } or latest_existing.requested_at > existing.requested_at)):
                    raise EvaluationConflict("a newer evaluation already exists")
                if existing.status is LeadFitEvaluationStatus.FAILED:
                    existing.status = LeadFitEvaluationStatus.PENDING
                    existing.error_code = None
                    existing.completed_at = None
                    self.db.commit()
                self._authorized_queue_reenqueue.add(existing.id)
            return existing

        latest = self._latest_attempt(workspace_id, lead_id)
        if latest is not None and latest.status in {
            LeadFitEvaluationStatus.PENDING, LeadFitEvaluationStatus.RUNNING,
        }:
            raise EvaluationConflict("an evaluation for this lead is already pending or running")
        if latest is not None and latest.status is LeadFitEvaluationStatus.FAILED and latest.call_count > 0:
            if retry_of is None:
                raise EvaluationConflict("provider failure requires retry_of and a new idempotency key")
            if retry_of != latest.id:
                raise EvaluationConflict("a newer evaluation already exists")

        cached = self._completed_cache(
            workspace_id=workspace_id,
            lead_id=lead_id,
            policy=policy,
            dossier=dossier,
            question_digest=question_digest,
        )
        if (cached is not None and latest is not None
                and latest.id == cached.id
                and latest.status is LeadFitEvaluationStatus.COMPLETED):
            return cached

        previous = None
        if retry_of is not None:
            if latest is not None and latest.id != retry_of:
                raise EvaluationConflict("a newer evaluation already exists")
            previous = self.db.execute(select(LeadFitEvaluation).where(
                LeadFitEvaluation.workspace_id == workspace_id,
                LeadFitEvaluation.lead_id == lead_id,
                LeadFitEvaluation.id == retry_of,
            ).with_for_update()).scalar_one_or_none()
            if previous is None or previous.status is not LeadFitEvaluationStatus.FAILED:
                raise EvaluationConflict("retry target is not a failed attempt for this lead")
            if previous.error_code == "enqueue_failed" and previous.call_count == 0:
                raise EvaluationConflict("enqueue_failed must reuse its stable request with reenqueue=true")
            if previous.policy_digest != policy.policy_digest or previous.source_digest != dossier.source_snapshot_digest:
                raise EvaluationConflict("retry target has stale policy or source snapshots")
        elif latest is not None and latest.error_code == "enqueue_failed" and latest.call_count == 0:
            raise EvaluationConflict("enqueue_failed requires the same idempotency key and explicit reenqueue=true")

        evaluation = LeadFitEvaluation(
            id=uuid4(), workspace_id=workspace_id, lead_id=lead_id,
            policy_id=policy.id, research_id=dossier.research_id,
            previous_id=previous.id if previous else None,
            policy_digest=policy.policy_digest,
            source_digest=dossier.source_snapshot_digest,
            question_digest=question_digest,
            engine_version=ENGINE_VERSION,
            requested_model=self.requested_model,
            response_model=None, result_digest=None,
            idempotency_key=intent_key,
            status=LeadFitEvaluationStatus.PENDING,
            classification=None, fit_score=None, coverage=None, result=None,
            audit={
                "requested_by": str(user_id),
                "action_key_hash": hashlib.sha256(idempotency_key.encode()).hexdigest(),
                "question_digest": question_digest,
                "expected_policy_digest": policy.policy_digest,
                "expected_source_digest": dossier.source_snapshot_digest,
            },
            call_count=0, budget_reserved_usd=None, budget_consumed_usd=None,
        )
        self.db.add(evaluation)
        self.db.flush()
        return evaluation

    def mark_enqueue_failed(self, evaluation_id: UUID) -> LeadFitEvaluation:
        """Persist queue failure without manufacturing a job or commercial result."""
        evaluation = self._evaluation(evaluation_id, lock=True)
        if evaluation is not None and evaluation.status is LeadFitEvaluationStatus.PENDING:
            evaluation.status = LeadFitEvaluationStatus.FAILED
            evaluation.error_code = "enqueue_failed"
            evaluation.completed_at = datetime.now(UTC)
            evaluation.classification = None
            evaluation.result = None
            self.db.commit()
        return evaluation

    def consume_queue_reenqueue_permission(self, evaluation_id: UUID) -> bool:
        """Consume the one-request permission created by a validated explicit reenqueue."""
        if evaluation_id not in self._authorized_queue_reenqueue:
            return False
        self._authorized_queue_reenqueue.remove(evaluation_id)
        return True

    def execute(self, evaluation_id: UUID) -> LeadFitEvaluation | None:
        """Run one claimed evaluation; no caller data can influence its assessment."""
        evaluation = self._evaluation(evaluation_id, lock=True)
        if evaluation is None:
            return None
        if evaluation.status is not LeadFitEvaluationStatus.PENDING:
            return evaluation
        lease = str(uuid4())
        evaluation.status = LeadFitEvaluationStatus.RUNNING
        evaluation.lease_owner = lease
        evaluation.leased_at = datetime.now(UTC)
        evaluation.started_at = datetime.now(UTC)
        self.db.commit()

        try:
            policy = self._verified_policy(evaluation)
            dossier = self._load_dossier(evaluation, policy)
            if dossier.source_snapshot_digest != evaluation.source_digest:
                return self._fail(evaluation, "stale_context")
            content = FitPolicyContent.model_validate(policy.content)
            dossier = fresh_dossier(content, dossier)
            if _question_plan_digest(content, dossier) != evaluation.question_digest:
                return self._fail(evaluation, "stale_context")
            source_answers: list[SourceAssessment] = []
            entity_questions = build_entity_questions(dossier)
            response_models: list[str] = []
            costs: list[float | None] = []

            if entity_questions:
                entity_call = self._dispatch(evaluation, lease, dossier, entity_questions)
                response_models.append(entity_call.response_model or "")
                costs.append(entity_call.cost_usd)
                entity_by_id = {source.evidence_id: source for source in dossier.sources}
                for qid in entity_questions:
                    source_id = qid.removeprefix("entity_")
                    source = entity_by_id.get(source_id)
                    choice = entity_call.answers.get(qid)
                    if source is None or choice is None:
                        raise ValueError("Jev entity response mapping mismatch")
                    status_map = {
                        "same_business": AssessmentResult.MATCHES,
                        "different_business": AssessmentResult.DOES_NOT_MATCH,
                        "insufficient": AssessmentResult.NOT_ESTABLISHED,
                    }
                    source_answers.append(SourceAssessment(
                        criterion_id="__entity__", evidence_id=source_id,
                        status=status_map[choice.choice], method="jev",
                        confidence=choice.confidence,
                        probabilities={
                            "matches": choice.probabilities["same_business"],
                            "does_not_match": choice.probabilities["different_business"],
                            "not_established": choice.probabilities["insufficient"],
                        },
                    ))
                from app.modules.lead_fit.evidence import bind_evidence_subjects

                dossier = bind_evidence_subjects(
                    dossier,
                    {f"entity_{answer.evidence_id}": answer for answer in source_answers},
                    entity_confidence_floor=content.entity_confidence_floor,
                )

            exact_assessments = self._exact_assessments(content, dossier)
            source_answers.extend(exact_assessments)
            criterion_questions = build_criterion_questions(content, dossier)
            if criterion_questions:
                criterion_call = self._dispatch(evaluation, lease, dossier, criterion_questions)
                response_models.append(criterion_call.response_model or "")
                costs.append(criterion_call.cost_usd)
                mapping = self._question_mapping(content, dossier)
                for qid in criterion_questions:
                    target = mapping.get(qid)
                    choice = criterion_call.answers.get(qid)
                    if target is None or choice is None:
                        raise ValueError("Jev criterion response mapping mismatch")
                    source_answers.append(SourceAssessment(
                        criterion_id=target[0], evidence_id=target[1],
                        status=AssessmentResult(choice.choice), method="jev",
                        confidence=choice.confidence, probabilities=choice.probabilities,
                    ))

            aggregated = aggregate_assessments(content, tuple(source_answers))
            identity_status = "matched" if any(s.subject_status == "matched" for s in dossier.sources) else "unresolved"
            try:
                authority = self.policy_service.verified_authority(
                    evaluation.workspace_id, policy.playbook_id,
                )
            except (PolicyAuthorityNotFound, PolicyAuthorityStale) as exc:
                raise EvaluationConflict("stale_policy") from exc
            allowed_channels = tuple(authority.context.communication.allowed_channels)
            decision = classify_fit(content, identity_status, aggregated, dossier.contacts, allowed_channels)
            contact_readiness = {
                channel: any(
                    contact.channel == channel
                    and contact.subject_status == "matched"
                    and contact.valid_format
                    for contact in dossier.contacts
                )
                for channel in allowed_channels
            }

            # Revalidate authority and exact source material immediately before persistence.
            current_policy = self._current_policy(evaluation.workspace_id, policy.playbook_id)
            revalidated_dossier = self._load_dossier(evaluation, policy)
            if (current_policy is None or current_policy.id != policy.id
                    or current_policy.policy_digest != evaluation.policy_digest
                    or revalidated_dossier.source_snapshot_digest != evaluation.source_digest
                    or _question_plan_digest(content, revalidated_dossier) != evaluation.question_digest):
                return self._fail(evaluation, "stale_context")

            result = {
                "classification": decision.classification.value,
                "fit_score": decision.fit_score,
                "coverage": decision.coverage,
                "assessments": [item.model_dump(mode="json") for item in aggregated],
                "identity_assessments": [
                    {
                        "evidence_id": answer.evidence_id,
                        "status": answer.status.value,
                        "confidence": answer.confidence,
                        "method": answer.method,
                    }
                    for answer in source_answers if answer.criterion_id == "__entity__"
                ],
                "reasons": list(decision.reasons),
                "missing_criterion_ids": list(decision.missing_criterion_ids),
                "contact_ready": decision.contact_ready,
                "contact_readiness": contact_readiness,
                "policy_digest": evaluation.policy_digest,
                "source_digest": evaluation.source_digest,
                "question_digest": evaluation.question_digest,
                "engine_version": ENGINE_VERSION,
            }
            evaluation.status = LeadFitEvaluationStatus.COMPLETED
            evaluation.classification = decision.classification
            evaluation.fit_score = decision.fit_score
            evaluation.coverage = decision.coverage
            evaluation.result = result
            evaluation.result_digest = canonical_sha256(result)
            evaluation.response_model = next((item for item in reversed(response_models) if item), None)
            cost_known = bool(costs) and len(costs) == evaluation.call_count and all(
                cost is not None for cost in costs
            )
            evaluation.budget_consumed_usd = sum(costs) if cost_known else None
            evaluation.audit = {
                **(evaluation.audit or {}),
                "dispatch_count": evaluation.call_count,
                "response_models": response_models,
                "cost_known": cost_known,
                "completed_at": datetime.now(UTC).isoformat(),
            }
            evaluation.completed_at = datetime.now(UTC)
            self.db.commit()
            return evaluation
        except Exception as exc:
            return self._fail(evaluation, _safe_error_code(exc), error_type=type(exc).__name__)

    def latest(self, workspace_id: UUID, lead_id: UUID) -> LeadFitEvaluation | None:
        return self.db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.lead_id == lead_id,
        ).order_by(LeadFitEvaluation.requested_at.desc()).limit(1)).scalar_one_or_none()

    def _dispatch(self, evaluation, lease, dossier, questions):
        claimed = self._evaluation(evaluation.id, lock=True)
        if claimed is None or claimed.lease_owner != lease or claimed.status is not LeadFitEvaluationStatus.RUNNING:
            raise EvaluationConflict("evaluation lease lost")
        if claimed.call_count >= 2:
            raise EvaluationConflict("JeV call budget exhausted")
        claimed.call_count += 1
        claimed.dispatched_at = datetime.now(UTC)
        audit = dict(claimed.audit or {})
        dispatch_costs = list(audit.get("dispatch_costs_usd", []))
        dispatch_costs.append(None)  # Durable unknown reservation until a response reports cost.
        response_models = list(audit.get("response_models", []))
        response_models.append(None)
        audit.update({
            "dispatch_costs_usd": dispatch_costs,
            "response_models": response_models,
            "last_dispatch_at": claimed.dispatched_at.isoformat(),
        })
        _update_cost_audit(audit)
        claimed.audit = audit
        claimed.budget_consumed_usd = None
        self.db.commit()  # Lock/reservation is durable before the provider request.
        provider = self._jev_factory()
        state = {
            "target": {"name": dossier.name or "Negocio del expediente"},
            "evidence": [
                {
                    "id": source.evidence_id,
                    "excerpt": source.excerpt[:EVIDENCE_EXCERPT_MAX_CHARS],
                    "facts": {
                        key: value for key, value in source.structured_facts.items()
                        if key != "contacts"
                    },
                    "truncated": source.truncated,
                }
                for source in dossier.sources if source.subject_status != "mismatched"
            ],
        }
        response = provider.evaluate(state, questions)
        self._record_dispatch_result(evaluation.id, lease, response)
        return response

    def _record_dispatch_result(self, evaluation_id, lease, response):
        claimed = self._evaluation(evaluation_id, lock=True)
        if claimed is None or claimed.lease_owner != lease or claimed.status is not LeadFitEvaluationStatus.RUNNING:
            raise EvaluationConflict("evaluation lease lost after provider response")
        audit = dict(claimed.audit or {})
        costs = list(audit.get("dispatch_costs_usd", []))
        models = list(audit.get("response_models", []))
        if not costs or len(costs) != claimed.call_count or len(models) != claimed.call_count:
            raise EvaluationConflict("dispatch audit is inconsistent")
        costs[-1] = _known_cost(response.cost_usd)
        models[-1] = response.response_model
        audit.update({"dispatch_costs_usd": costs, "response_models": models})
        _update_cost_audit(audit)
        claimed.audit = audit
        claimed.budget_consumed_usd = (
            audit["known_cost_usd"] if audit["cost_known"] else None
        )
        self.db.commit()

    def _verified_policy(self, evaluation):
        policy = self.db.execute(select(LeadFitPolicy).where(
            LeadFitPolicy.workspace_id == evaluation.workspace_id,
            LeadFitPolicy.id == evaluation.policy_id,
        )).scalar_one_or_none()
        if policy is None:
            raise EvaluationConflict("policy disappeared")
        current = self._current_policy(evaluation.workspace_id, policy.playbook_id)
        if current is None or current.id != policy.id or policy.policy_digest != evaluation.policy_digest:
            raise EvaluationConflict("stale_policy")
        return policy

    def _current_policy(self, workspace_id, playbook_id):
        try:
            return self.policy_service.current(workspace_id, playbook_id)
        except (PolicyAuthorityNotFound, PolicyAuthorityStale):
            return None

    def _load_dossier(self, evaluation, policy):
        return self._dossier_loader(
            self.db, workspace_id=evaluation.workspace_id,
            lead_id=evaluation.lead_id, policy=policy,
        )

    def _latest_attempt(self, workspace_id, lead_id):
        return self.db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.lead_id == lead_id,
        ).order_by(LeadFitEvaluation.requested_at.desc()).limit(1)).scalar_one_or_none()

    def _completed_cache(self, *, workspace_id, lead_id, policy, dossier, question_digest):
        if not dossier.sources or not all(
            source.retrieved_at >= datetime.now(UTC) - timedelta(hours=policy.content["max_evidence_age_hours"])
            for source in dossier.sources
        ):
            return None
        cached = self.db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.lead_id == lead_id,
            LeadFitEvaluation.policy_id == policy.id,
            LeadFitEvaluation.policy_digest == policy.policy_digest,
            LeadFitEvaluation.source_digest == dossier.source_snapshot_digest,
            LeadFitEvaluation.question_digest == question_digest,
            LeadFitEvaluation.engine_version == ENGINE_VERSION,
            LeadFitEvaluation.requested_model == self.requested_model,
            LeadFitEvaluation.status == LeadFitEvaluationStatus.COMPLETED,
        ).order_by(LeadFitEvaluation.requested_at.desc()).limit(1)).scalar_one_or_none()
        if (cached is None or cached.classification is None or cached.result is None
                or cached.result_digest != canonical_sha256(cached.result)):
            return None
        return cached

    def _evaluation(self, evaluation_id, *, lock=False):
        statement = select(LeadFitEvaluation).where(LeadFitEvaluation.id == evaluation_id)
        if lock:
            statement = statement.with_for_update()
        return self.db.execute(statement).scalar_one_or_none()

    def _fail(self, evaluation, error_code, *, error_type=None):
        evaluation.status = LeadFitEvaluationStatus.FAILED
        evaluation.classification = None
        evaluation.fit_score = None
        evaluation.coverage = None
        evaluation.result = None
        evaluation.result_digest = None
        evaluation.error_code = error_code
        evaluation.completed_at = datetime.now(UTC)
        evaluation.audit = {
            **(evaluation.audit or {}),
            "dispatch_count": evaluation.call_count,
            "failed_at": evaluation.completed_at.isoformat(),
            **({"error_type": error_type} if error_type else {}),
        }
        self.db.commit()
        return evaluation

    @staticmethod
    def _exact_assessments(policy, dossier):
        answers = []
        fresh_after = datetime.now(UTC) - timedelta(hours=policy.max_evidence_age_hours)
        for criterion in policy.criteria:
            if criterion.evaluator is not CriterionEvaluator.EXACT:
                continue
            for source in dossier.sources:
                if source.subject_status != "matched" or source.retrieved_at < fresh_after:
                    continue
                result = evaluate_exact_rule(criterion, source.structured_facts)
                if result is AssessmentResult.NOT_ESTABLISHED:
                    continue
                answers.append(SourceAssessment(
                    criterion_id=criterion.id, evidence_id=source.evidence_id,
                    status=result, method="code", confidence=1.0,
                ))
        return answers

    @staticmethod
    def _question_mapping(policy, dossier):
        dossier = fresh_dossier(policy, dossier)
        mapping = {}
        for criterion in policy.criteria:
            if criterion.evaluator is not CriterionEvaluator.SEMANTIC:
                continue
            for source in dossier.sources:
                if source.subject_status == "matched":
                    mapping[f"criterion_{criterion.id}_{source.evidence_id}"] = (criterion.id, source.evidence_id)
        return mapping


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, JevClientError):
        # Keep stable operational categories; do not store raw exception/provider data.
        return "timeout" if "timeout" in error.message.lower() or error.error_code == "timeout" else "provider_error"
    if isinstance(error, EvaluationConflict) and any(
        marker in str(error).lower() for marker in ("stale", "policy")
    ):
        return "stale_context"
    if isinstance(error, DossierError):
        return "stale_context"
    return "provider_error"


def _question_plan_digest(policy: FitPolicyContent, dossier) -> str:
    """Hash both planned Jev phases, including possible sources resolved in phase one."""
    dossier = fresh_dossier(policy, dossier)
    potential_sources = tuple(
        source.model_copy(update={"subject_status": "matched"})
        for source in dossier.sources
    )
    potential_dossier = dossier.model_copy(update={"sources": potential_sources})
    return canonical_sha256({
        "entity": build_entity_questions(dossier),
        "criteria": build_criterion_questions(policy, potential_dossier),
    })


def _known_cost(value: object) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _update_cost_audit(audit: dict) -> None:
    costs = audit.get("dispatch_costs_usd", [])
    known = [cost for cost in costs if _known_cost(cost) is not None]
    audit["known_cost_usd"] = sum(known)
    audit["unknown_cost_dispatches"] = sum(cost is None for cost in costs)
    audit["cost_known"] = bool(costs) and len(known) == len(costs)
