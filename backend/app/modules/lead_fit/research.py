"""Server-derived previews and durable, bounded incremental research grants."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.modules.lead_fit.contracts import FitResearchPlan, canonical_sha256
from app.modules.lead_fit.dossier import load_server_dossier
from app.modules.lead_fit.evaluation_service import LeadFitEvaluationService
from app.modules.lead_fit.models import (
    LeadFitEvaluation,
    LeadFitEvaluationStatus,
    LeadFitPolicy,
    LeadFitResearchGrant,
    LeadFitResearchGrantStatus,
)
from app.modules.lead_fit.service import LeadFitPolicyService
from app.modules.leads.models import Lead
from app.modules.prospect_research.models import (
    ProspectResearch,
    ProspectResearchStatus,
    ResearchEvidence,
)
from app.modules.prospect_research.service import _research_url

PLAN_TTL = timedelta(minutes=15)
GRANT_LEASE_TIMEOUT = timedelta(minutes=15)
MAX_DOSSIER_SOURCES = 6
MAX_RESEARCH_TOPIC_LENGTH = 140
_EMAIL = re.compile(
    r"(?i)(?<![\w.+-])(?=[\w.+-]{1,128}@)[\w+-]+(?:\.[\w+-]+)*@"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}"
    r"(?=$|[\s,;:!?\"'()\[\]{}<>]|\.(?=$|[\s,;:!?\"'()\[\]{}<>]))"
)
_WHATSAPP = re.compile(r"(?i)https?://(?:wa\.me/\d{7,}|(?:www\.)?api\.whatsapp\.com/send\?[^\s<>\"']+)")
_TEL_PHONE = re.compile(r"(?i)(?<![\w])tel:\s*(\+?\d[\d(). -]{5,}\d)")
_CRITERION_FRAMING = re.compile(
    r"^(?:El negocio pertenece a una de estas industrias|"
    r"El negocio opera en una de estas geografías|"
    r"El comprador objetivo tiene uno de estos roles|"
    r"El negocio muestra uno de estos rasgos deseados|"
    r"Oportunidad observable|Necesidad observable|"
    r"Se prioriza al negocio cuando presenta esta señal|"
    r"Se prioriza menos al negocio cuando presenta esta señal)\s*:\s*",
    re.IGNORECASE,
)
_RESEARCH_INSTRUCTION = re.compile(r"\bCómo comprobar\s*:", re.IGNORECASE)
_SEARCH_OPERATOR = re.compile(
    r"(?:(?<!\w)(?:AND|OR|NOT)(?!\w)|(?i:\b(?:site|inurl|intitle|filetype|cache|related|"
    r"allintext|allintitle|allinurl|inanchor)\s*:)|"
    r"(^|\s)-\w|[\"{}])"
)


def _approved_research_topic(description: object) -> str:
    """Extract a short, factual search topic from approved criterion text."""
    if not isinstance(description, str) or not description.strip():
        raise ResearchGrantConflict("criterion has no safe research topic")
    if any(ord(character) < 32 or ord(character) == 127 for character in description):
        raise ResearchGrantConflict("criterion has no safe research topic")

    topic = _RESEARCH_INSTRUCTION.split(description, maxsplit=1)[0]
    topic = re.split(r"[!?](?:\s|$)|\.(?=\s+[A-ZÁÉÍÓÚÜÑ])", topic, maxsplit=1)[0]
    topic = _CRITERION_FRAMING.sub("", topic.strip(), count=1)
    topic = re.sub(r"\s+", " ", topic).strip(" \t\r\n.,;:!?-")
    if (
        not topic
        or _EMAIL.search(topic)
        or re.search(r"(?i)https?://|\bwww\.", topic)
        or _SEARCH_OPERATOR.search(topic)
    ):
        raise ResearchGrantConflict("criterion has no safe research topic")
    if len(topic) > MAX_RESEARCH_TOPIC_LENGTH:
        bounded_topic = topic[:MAX_RESEARCH_TOPIC_LENGTH]
        if " " not in bounded_topic:
            raise ResearchGrantConflict("criterion has no safe research topic")
        topic = bounded_topic.rsplit(" ", 1)[0].rstrip()
    if not topic:
        raise ResearchGrantConflict("criterion has no safe research topic")
    return topic


class ResearchGrantConflict(ValueError):
    """The reviewed plan or current authority no longer matches server state."""


def _source_relevance_hint(business, content: str) -> int:
    """Rank textual identity hints, never establish attribution or commercial fit.

    Shared locations, website ownership, and provider rank are not identity
    evidence. Jev must still evaluate each selected material independently.
    """
    def words(value):
        folded = unicodedata.normalize("NFKD", value.casefold())
        return re.findall(r"\w+", "".join(
            character for character in folded if not unicodedata.combining(character)
        ))

    name = words(getattr(business, "name", None) or "")
    # Rank only original body bytes also available to the evaluator. Provider
    # titles are retained as metadata, not silently treated as visible evidence.
    text = content[:4000]
    normalized = " " + " ".join(words(text)) + " "
    name_hint = bool(name and f" {' '.join(name)} " in normalized)
    phone = re.sub(r"\D", "", getattr(business, "phone", None) or "")
    phone_hint = bool(len(phone) >= 7 and any(
        re.sub(r"\D", "", match.group()) == phone
        for match in re.finditer(r"(?<!\d)\+?\d[\d ()+.\-]{5,}\d(?!\d)", text)
    ))
    return int(name_hint) + int(phone_hint)


def _verified_active_identity_anchor(context, active_tavily, prior_materials):
    """Return the strongest currently verified, unchanged active Tavily source."""
    evaluation = getattr(context, "evaluation", None)
    result = getattr(evaluation, "result", None)
    assessments = result.get("identity_assessments") if isinstance(result, dict) else None
    if not isinstance(assessments, list):
        return None

    policy = getattr(context, "policy", None)
    content = getattr(policy, "content", None)
    floor = content.get("entity_confidence_floor", 0.90) if isinstance(content, dict) else 0.90
    if (
        not isinstance(floor, int | float) or isinstance(floor, bool)
        or not 0 <= floor <= 1
    ):
        return None

    confidence_by_id = {}
    seen_ids = set()
    duplicate_ids = set()
    for item in assessments:
        if not isinstance(item, dict) or not isinstance(item.get("evidence_id"), str):
            continue
        evidence_id = item["evidence_id"]
        if evidence_id in seen_ids or evidence_id in duplicate_ids:
            confidence_by_id.pop(evidence_id, None)
            duplicate_ids.add(evidence_id)
            continue
        seen_ids.add(evidence_id)
        confidence = item.get("confidence")
        if (
            item.get("status") == "matches"
            and isinstance(confidence, int | float)
            and not isinstance(confidence, bool)
            and 0 <= confidence <= 1
            and confidence >= floor
        ):
            confidence_by_id[evidence_id] = confidence

    candidates = []
    for source in active_tavily:
        evidence_id = getattr(source, "evidence_id", None)
        source_type = getattr(source, "type", None)
        source_url = getattr(source, "source_url", None)
        material_digest = getattr(source, "original_material_digest", None)
        confidence = confidence_by_id.get(evidence_id)
        indexed = prior_materials.get((source_type, source_url, material_digest))
        if (
            confidence is not None
            and isinstance(material_digest, str)
            and indexed is not None
            and indexed.get("evidence_id") == evidence_id
        ):
            candidates.append((confidence, source_type, source_url, evidence_id))
    if not candidates:
        return None
    # One anchor is reserved; ties use stable material identity keys.
    return min(candidates, key=lambda item: (-item[0], item[1], item[2], item[3]))[3]


class LeadFitResearchService:
    """Preview, authorize and execute a single durable research round."""

    def __init__(
        self,
        db,
        *,
        context_loader: Callable | None = None,
        policy_service=None,
        tavily_provider=None,
        evaluation_service_factory: Callable | None = None,
        clock: Callable[[], datetime] | None = None,
        jev_factory: Callable | None = None,
    ) -> None:
        self.db = db
        self._context_loader = context_loader or self._load_context
        self._policy_service = policy_service
        self._tavily_provider = tavily_provider
        self._evaluation_service_factory = evaluation_service_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        # Public for deterministic fake-clock contract tests.
        self.clock = self._clock
        self._jev_factory = jev_factory

    def preview(
        self, *, workspace_id: UUID, lead_id: UUID, evaluation_id: UUID, purpose: str = "fit",
    ) -> FitResearchPlan:
        if purpose not in {"fit", "contact"}:
            raise ResearchGrantConflict("unsupported research purpose")
        context = self._context_loader(
            workspace_id=workspace_id, lead_id=lead_id, evaluation_id=evaluation_id,
        )
        evaluation = context.evaluation
        if evaluation.status is not LeadFitEvaluationStatus.COMPLETED and getattr(
            evaluation.status, "value", evaluation.status
        ) != LeadFitEvaluationStatus.COMPLETED.value:
            raise ResearchGrantConflict("research requires a completed persisted evaluation")
        if evaluation.workspace_id != workspace_id or evaluation.lead_id != lead_id:
            raise ResearchGrantConflict("evaluation scope mismatch")

        result = evaluation.result or {}
        criteria = context.policy.content.get("criteria", ())
        descriptions = {
            item.get("id"): item.get("description")
            for item in criteria if isinstance(item, dict)
        }
        if purpose == "fit":
            missing = tuple(
                criterion_id for criterion_id in result.get("missing_criterion_ids", ())
                if criterion_id in descriptions
            )
            if not missing:
                raise ResearchGrantConflict("no unresolved fit criterion can benefit from research")
            topic = _approved_research_topic(descriptions[missing[0]])
            # Maps identifies the stored lead, not every web page returned for
            # its sector. First locate attributable web evidence; only then
            # constrain a later, separately reviewed search by a fit criterion.
            query_purpose = (
                f"verificar {topic}"
                if self._identity_anchor_for_preview(context) is not None else ""
            )
        else:
            readiness = result.get("contact_readiness")
            if not isinstance(readiness, dict):
                raise ResearchGrantConflict("current contact readiness is unavailable")
            missing_channels = tuple(
                channel for channel, ready in readiness.items() if ready is False
            )
            if not missing_channels:
                raise ResearchGrantConflict("no unresolved contact channel can benefit from research")
            missing = ()
            channels = " ".join(
                re.sub(r"\s+", " ", channel).strip()
                for channel in missing_channels
                if isinstance(channel, str) and channel.strip()
            )
            query_purpose = "contacto público"
            if channels:
                query_purpose = f"{query_purpose} {channels}"
        name = context.business.name
        if not isinstance(name, str) or not name.strip():
            raise ResearchGrantConflict("business name is required for a research query")
        identity = re.sub(r"\s+", " ", name).strip()
        address = getattr(context.business, "address", None)
        location = (
            re.sub(r"\s+", " ", address).strip()
            if isinstance(address, str) else ""
        )
        # Keep identity and purpose intact; only bound the optional location.
        location_budget = 500 - len(identity) - len(query_purpose) - 2
        if location_budget < 0:
            raise ResearchGrantConflict("business identity and research topic exceed the query limit")
        location = location[:location_budget].rstrip()
        query = " ".join(
            part for part in (identity, location, query_purpose) if part
        )

        # A fixed clock bucket keeps repeated preview/authorize derivations stable.
        current = self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ResearchGrantConflict("preview clock must be timezone-aware")
        bucket = int(current.astimezone(UTC).timestamp()) // 300
        expires_at = datetime.fromtimestamp((bucket + 2) * 300, UTC)
        plan_data = {
            "lead_id": lead_id,
            "evaluation_id": evaluation_id,
            "policy_id": context.policy.id,
            "source_digest": evaluation.source_digest,
            "purpose": purpose,
            "missing_criterion_ids": missing,
            "query": query,
            "source_strategy": "web",
            "max_search": 1,
            "max_extract": 1,
            "max_jev": 2,
            "max_rounds": 1,
            "monetary_cap_usd": None,
            "expires_at": expires_at,
        }
        plan_payload = FitResearchPlan(
            **plan_data, plan_hash="0" * 64,
        ).model_dump(mode="json", exclude={"plan_hash"})
        plan_hash = canonical_sha256(plan_payload)
        return FitResearchPlan(**plan_data, plan_hash=plan_hash)

    def _identity_anchor_for_preview(self, context):
        current = self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ResearchGrantConflict("preview clock must be timezone-aware")
        cutoff = current - timedelta(
            hours=context.policy.content.get("max_evidence_age_hours", 720),
        )
        sources = [
            source for source in getattr(context.dossier, "sources", ())
            if source.provider == "tavily" and source.retrieved_at >= cutoff
        ]
        if not sources:
            return None
        prior_materials = self._prior_material_index(
            context, workspace_id=context.evaluation.workspace_id,
            lead_id=context.evaluation.lead_id, freshness_cutoff=cutoff,
        )
        return _verified_active_identity_anchor(context, sources, prior_materials)

    def authorize(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        lead_id: UUID,
        evaluation_id: UUID,
        purpose: str,
        expected_plan_hash: str,
        confirmed: bool,
        idempotency_key: str,
        monetary_cap_usd: float | None = None,
    ) -> LeadFitResearchGrant:
        if not confirmed:
            raise ResearchGrantConflict("explicit confirmation is required")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ResearchGrantConflict("invalid idempotency key")
        # Lead-row lock serializes active-grant and idempotency decisions.
        lead = self.db.execute(select(Lead).where(
            Lead.workspace_id == workspace_id, Lead.id == lead_id,
        ).with_for_update(of=Lead)).scalar_one_or_none()
        if lead is None:
            raise ResearchGrantConflict("lead not found")
        existing = self.db.execute(select(LeadFitResearchGrant).where(
            LeadFitResearchGrant.workspace_id == workspace_id,
            LeadFitResearchGrant.idempotency_key == idempotency_key,
        ).with_for_update()).scalar_one_or_none()
        if existing is not None:
            self._validate_idempotent_replay(
                existing, workspace_id=workspace_id, lead_id=lead_id,
                evaluation_id=evaluation_id, purpose=purpose,
                expected_plan_hash=expected_plan_hash,
                monetary_cap_usd=monetary_cap_usd,
            )
            return existing
        plan = self.preview(
            workspace_id=workspace_id, lead_id=lead_id,
            evaluation_id=evaluation_id, purpose=purpose,
        )
        if plan.plan_hash != expected_plan_hash:
            raise ResearchGrantConflict("research plan changed; refresh preview")
        if self.clock() >= plan.expires_at:
            raise ResearchGrantConflict("research plan expired")
        if monetary_cap_usd is not None and monetary_cap_usd < 0:
            raise ResearchGrantConflict("monetary cap must be nonnegative")
        active = self.db.execute(select(LeadFitResearchGrant).where(
            LeadFitResearchGrant.workspace_id == workspace_id,
            LeadFitResearchGrant.lead_id == lead_id,
            LeadFitResearchGrant.status.in_((
                LeadFitResearchGrantStatus.AUTHORIZED,
                LeadFitResearchGrantStatus.RUNNING,
            )),
        ).with_for_update()).scalar_one_or_none()
        if active is not None:
            lease_at = (
                active.leased_at if active.status is LeadFitResearchGrantStatus.RUNNING
                else active.dispatched_at if active.status is LeadFitResearchGrantStatus.AUTHORIZED
                else None
            )
            if (
                lease_at is not None and lease_at.tzinfo is not None
                and self.clock() - lease_at >= GRANT_LEASE_TIMEOUT
            ):
                # Only a fresh user authorization closes an abandoned lease.
                # The old row stays terminal with its reservations and is never requeued.
                active.status = LeadFitResearchGrantStatus.FAILED
                active.error_code = (
                    "worker_lease_expired"
                    if active.leased_at is not None else "queue_lease_expired"
                )
                active.lease_owner = None
                active = None
        if active is not None:
            raise ResearchGrantConflict("a research grant is already active for this lead")

        grant = LeadFitResearchGrant(
            id=uuid4(), workspace_id=workspace_id, lead_id=lead_id,
            evaluation_id=evaluation_id, policy_id=plan.policy_id, actor_id=user_id,
            source_digest=plan.source_digest, plan_hash=plan.plan_hash,
            plan=plan.model_dump(mode="json"), idempotency_key=idempotency_key,
            max_search=1, max_extract=1, max_jev=2, max_rounds=1,
            monetary_cap_usd=monetary_cap_usd,
            consumed_search=0, consumed_extract=0, consumed_jev=0,
            reserved_search=0, reserved_extract=0, reserved_jev=0,
            consumed_usd=None, status=LeadFitResearchGrantStatus.AUTHORIZED,
            unchanged_rounds=0, expires_at=plan.expires_at,
        )
        self.db.add(grant)
        self.db.flush()
        return grant

    def _validate_idempotent_replay(
        self, grant, *, workspace_id, lead_id, evaluation_id, purpose,
        expected_plan_hash, monetary_cap_usd,
    ) -> None:
        if (
            grant.workspace_id != workspace_id
            or grant.lead_id != lead_id
            or grant.evaluation_id != evaluation_id
            or grant.plan_hash != expected_plan_hash
            or grant.monetary_cap_usd != monetary_cap_usd
            or not isinstance(grant.plan, dict)
            or grant.plan.get("purpose") != purpose
            or grant.plan.get("lead_id") != str(lead_id)
            or grant.plan.get("evaluation_id") != str(evaluation_id)
            or grant.plan.get("policy_id") != str(grant.policy_id)
            or grant.plan.get("source_digest") != grant.source_digest
        ):
            raise ResearchGrantConflict("idempotency key is bound to different scope, plan, or caps")
        caps = {
            "max_search": (grant.max_search, 1),
            "max_extract": (grant.max_extract, 1),
            "max_jev": (grant.max_jev, 2),
            "max_rounds": (grant.max_rounds, 1),
        }
        if any(actual != expected or grant.plan.get(name) != expected
               for name, (actual, expected) in caps.items()):
            raise ResearchGrantConflict("stored grant caps do not match its reviewed plan")
        plan_material = dict(grant.plan)
        stored_hash = plan_material.pop("plan_hash", None)
        if stored_hash != grant.plan_hash or canonical_sha256(plan_material) != grant.plan_hash:
            raise ResearchGrantConflict("stored grant plan integrity check failed")
        evaluation = self.db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.lead_id == lead_id,
            LeadFitEvaluation.id == evaluation_id,
        )).scalar_one_or_none()
        if (
            evaluation is None or evaluation.policy_id != grant.policy_id
            or evaluation.source_digest != grant.source_digest
        ):
            raise ResearchGrantConflict("idempotent grant evaluation binding changed")

    def status(self, *, workspace_id: UUID, lead_id: UUID, grant_id: UUID):
        return self.db.execute(select(LeadFitResearchGrant).where(
            LeadFitResearchGrant.workspace_id == workspace_id,
            LeadFitResearchGrant.lead_id == lead_id,
            LeadFitResearchGrant.id == grant_id,
        )).scalar_one_or_none()

    def mark_enqueue_failed(self, grant_id: UUID) -> LeadFitResearchGrant | None:
        """Mark a committed authorization terminal when queue dispatch fails."""
        grant = self._grant(grant_id, lock=True)
        if grant is not None and grant.status is LeadFitResearchGrantStatus.AUTHORIZED:
            grant.status = LeadFitResearchGrantStatus.FAILED
            grant.error_code = "enqueue_failed"
            self.db.commit()
        return grant

    def execute(self, grant_id: UUID) -> LeadFitResearchGrant | None:
        grant = self._grant(grant_id, lock=True)
        if grant is None or grant.status is not LeadFitResearchGrantStatus.AUTHORIZED:
            return grant
        if grant.expires_at is not None and self.clock() >= grant.expires_at:
            grant.status = LeadFitResearchGrantStatus.FAILED
            grant.error_code = "plan_expired"
            self.db.commit()
            return grant
        lease = str(uuid4())
        grant.status = LeadFitResearchGrantStatus.RUNNING
        grant.lease_owner = lease
        grant.leased_at = self.clock()
        self.db.commit()
        try:
            context = self._context_loader(
                workspace_id=grant.workspace_id,
                lead_id=grant.lead_id,
                evaluation_id=grant.evaluation_id,
            )
            if context.evaluation.policy_id != grant.policy_id or context.evaluation.source_digest != grant.source_digest:
                raise ResearchGrantConflict("current evaluation authority changed")
            self._perform_tavily_round(grant.id, lease, context)
            dossier = load_server_dossier(
                self.db, workspace_id=grant.workspace_id,
                lead_id=grant.lead_id, policy=context.policy,
            )
            evaluation_service = self._evaluation_service()
            current_context = evaluation_service.context(
                workspace_id=grant.workspace_id,
                lead_id=grant.lead_id,
                playbook_id=context.policy.playbook_id,
            )
            if (
                current_context.get("policy_id") != grant.policy_id
                or current_context.get("policy_digest") != context.policy.policy_digest
            ):
                raise ResearchGrantConflict("approved policy changed during research")
            intent = evaluation_service.request(
                workspace_id=grant.workspace_id, lead_id=grant.lead_id,
                user_id=grant.actor_id, policy_id=current_context["policy_id"],
                expected_policy_digest=current_context["policy_digest"],
                expected_source_digest=dossier.source_snapshot_digest,
                idempotency_key=f"research:{grant.id}",
            )
            grant = self._grant(grant.id, lock=True)
            grant.result_evaluation_id = intent.id
            self.db.commit()
            if intent.status is LeadFitEvaluationStatus.PENDING:
                evaluator = self._evaluation_service(jev_factory=self._budgeted_jev_factory(grant.id, lease))
                result = evaluator.execute(intent.id)
                if result is None or result.status is not LeadFitEvaluationStatus.COMPLETED:
                    raise ResearchGrantConflict("incremental evaluation did not complete")
            grant = self._grant(grant.id, lock=True)
            unchanged = dossier.source_snapshot_digest == grant.source_digest
            previous_rounds = self.db.execute(select(LeadFitResearchGrant).where(
                LeadFitResearchGrant.workspace_id == grant.workspace_id,
                LeadFitResearchGrant.lead_id == grant.lead_id,
                LeadFitResearchGrant.evaluation_id == grant.evaluation_id,
                LeadFitResearchGrant.id != grant.id,
                LeadFitResearchGrant.status.in_((
                    LeadFitResearchGrantStatus.COMPLETED,
                    LeadFitResearchGrantStatus.EXHAUSTED,
                )),
            )).scalars().all()
            prior_unchanged = max((item.unchanged_rounds for item in previous_rounds), default=0)
            grant.unchanged_rounds = prior_unchanged + 1 if unchanged else 0
            grant.status = (
                LeadFitResearchGrantStatus.EXHAUSTED
                if grant.unchanged_rounds >= 2
                else LeadFitResearchGrantStatus.COMPLETED
            )
            if grant.status is LeadFitResearchGrantStatus.EXHAUSTED:
                grant.error_code = "no_material_progress_inconclusive"
            grant.lease_owner = None
            self.db.commit()
            return grant
        except Exception as exc:
            grant = self._grant(grant_id, lock=True)
            if grant is not None and grant.status is LeadFitResearchGrantStatus.RUNNING and grant.lease_owner == lease:
                grant.status = LeadFitResearchGrantStatus.FAILED
                grant.error_code = self._safe_error_code(exc)
                grant.lease_owner = None
                self.db.commit()
            return grant

    def _load_context(self, *, workspace_id: UUID, lead_id: UUID, evaluation_id: UUID):
        evaluation = self.db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.lead_id == lead_id,
            LeadFitEvaluation.id == evaluation_id,
        )).scalar_one_or_none()
        if evaluation is None:
            raise ResearchGrantConflict("evaluation not found")
        if (
            evaluation.classification is None
            or not isinstance(evaluation.result, dict)
            or evaluation.result_digest != canonical_sha256(evaluation.result)
        ):
            raise ResearchGrantConflict("completed evaluation result integrity check failed")
        latest_attempt = self.db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.lead_id == lead_id,
        ).order_by(LeadFitEvaluation.requested_at.desc()).limit(1)).scalar_one_or_none()
        if (
            latest_attempt is None or latest_attempt.id != evaluation.id
            or latest_attempt.status is not LeadFitEvaluationStatus.COMPLETED
        ):
            raise ResearchGrantConflict("research preview must target the latest completed evaluation attempt")
        policy = self.db.execute(select(LeadFitPolicy).where(
            LeadFitPolicy.workspace_id == workspace_id,
            LeadFitPolicy.id == evaluation.policy_id,
        )).scalar_one_or_none()
        if policy is None or policy.policy_digest != evaluation.policy_digest:
            raise ResearchGrantConflict("evaluation policy is stale")
        authority = self._policy_service or LeadFitPolicyService(self.db)
        current = authority.current(workspace_id, policy.playbook_id)
        if current is None or current.id != policy.id:
            raise ResearchGrantConflict("current approved policy changed")
        lead = self.db.execute(select(Lead).options(joinedload(Lead.business)).where(
            Lead.workspace_id == workspace_id, Lead.id == lead_id,
        )).scalar_one_or_none()
        if lead is None or lead.business is None:
            raise ResearchGrantConflict("lead not found")
        dossier = load_server_dossier(
            self.db, workspace_id=workspace_id, lead_id=lead_id, policy=policy,
        )
        if dossier.source_snapshot_digest != evaluation.source_digest:
            raise ResearchGrantConflict("evaluation source snapshot is stale")
        return type("ResearchContext", (), {
            "evaluation": evaluation, "policy": policy, "lead": lead,
            "business": lead.business, "dossier": dossier,
        })()

    def _perform_tavily_round(self, grant_id: UUID, lease: str, context) -> None:
        from app.integrations.tavily import TavilyProvider

        provider = self._tavily_provider or TavilyProvider()
        grant = self._grant(grant_id, lock=True)
        plan = grant.plan
        self._reserve(grant_id, lease, "search")
        try:
            response = provider.search(plan["query"], max_results=10, search_depth="basic")
        except Exception:
            # A failed/ambiguous call remains reserved and cannot be replayed.
            raise
        search_usage = response.usage if isinstance(response.usage, dict) else {}
        self._consume(grant_id, lease, "search")

        now = self.clock()
        parent = None
        if context.evaluation.research_id is not None:
            parent = self.db.execute(select(ProspectResearch).where(
                ProspectResearch.workspace_id == grant.workspace_id,
                ProspectResearch.lead_id == grant.lead_id,
                ProspectResearch.id == context.evaluation.research_id,
            )).scalar_one_or_none()
        parent_created_at = getattr(parent, "created_at", None) if parent is not None else None
        if parent_created_at is not None and now <= parent_created_at:
            # Snapshot selection orders by creation time; keep ancestry deterministic
            # even when a fake/fixed clock or timestamp precision produces a tie.
            now = parent_created_at + timedelta(microseconds=1)
        research = ProspectResearch(
            id=uuid4(), workspace_id=grant.workspace_id, lead_id=grant.lead_id,
            refresh_of_id=context.evaluation.research_id,
            status=ProspectResearchStatus.RUNNING, provider="lead_fit_research",
            operation="incremental_research", query_summary=plan["query"],
            query_context={
                "grant_id": str(grant.id), "plan_hash": grant.plan_hash,
                "evaluation_id": str(grant.evaluation_id),
                "policy_id": str(grant.policy_id),
            },
            requested_at=now, started_at=now, created_at=now, updated_at=now,
        )
        self.db.add(research)
        self.db.flush()

        max_age_hours = context.policy.content.get("max_evidence_age_hours", 720)
        freshness_cutoff = now - timedelta(hours=max_age_hours)
        fresh_sources = [
            source for source in context.dossier.sources
            if source.retrieved_at >= freshness_cutoff
        ]
        fixed_ids = [
            source.evidence_id for source in fresh_sources
            if source.provider != "tavily"
        ]
        active_tavily = [
            source for source in fresh_sources if source.provider == "tavily"
        ]
        active_tavily_ids = [source.evidence_id for source in active_tavily]
        active_search_ids = [
            source.evidence_id for source in active_tavily if source.type == "search"
        ]
        expired_source_ids = [
            source.evidence_id for source in context.dossier.sources
            if source.retrieved_at < freshness_cutoff
        ]
        if len(fixed_ids) > MAX_DOSSIER_SOURCES:
            raise ResearchGrantConflict("current dossier exceeds its validated source selection cap")
        if len(fixed_ids) + len(active_tavily_ids) > MAX_DOSSIER_SOURCES:
            raise ResearchGrantConflict("current dossier exceeds its validated source selection cap")
        prior_materials = self._prior_material_index(
            context, workspace_id=grant.workspace_id, lead_id=grant.lead_id,
            freshness_cutoff=freshness_cutoff,
        )
        identity_anchor_id = _verified_active_identity_anchor(
            context, active_tavily, prior_materials,
        )
        batch_materials: dict[tuple[str, str, str], dict] = {}
        search_records: list[dict] = []
        invalid_url_count = 0
        for rank, item in enumerate(response.results[:10]):
            safe_url = _research_url(item.url)
            if safe_url is None:
                invalid_url_count += 1
                continue
            original_content = item.content or ""
            content = _contact_excerpt(original_content) if plan.get("purpose") == "contact" else original_content[:4000]
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            key = ("search", safe_url, digest)
            previous = prior_materials.get(key) or batch_materials.get(key)
            record = {
                "operation": "search", "url": safe_url, "content": content,
                "title": item.title, "digest": digest, "key": key,
                "evidence_id": previous["evidence_id"] if previous else self._evidence_id(
                    grant, research, "search", safe_url, digest,
                ),
                "existing": previous is not None,
                "provider_request_id": getattr(response, "request_id", None),
                "rank": rank,
            }
            search_records.append(record)
            batch_materials[key] = record

        extract_attempted = bool(
            search_records and len(fixed_ids) < MAX_DOSSIER_SOURCES and grant.max_extract > 0
        )
        search_slots = max(
            0, MAX_DOSSIER_SOURCES - len(fixed_ids) - (1 if extract_attempted else 0),
        )
        novel_search = [record for record in search_records if not record["existing"]]
        if novel_search:
            ordered_search = [
                *novel_search,
                *(record for record in search_records
                  if record["existing"] and record["evidence_id"] in active_search_ids),
                *(record for record in search_records
                  if record["existing"] and record["evidence_id"] not in active_search_ids),
            ]
        else:
            active_search_order = {
                evidence_id: index for index, evidence_id in enumerate(active_search_ids)
            }
            ordered_search = sorted(
                search_records,
                key=lambda record: (
                    0 if record["evidence_id"] in active_search_order else 1,
                    active_search_order.get(record["evidence_id"], len(active_search_order)),
                    record["rank"],
                ),
            )
        # Relevance precedes novelty, while equal hints retain the established
        # stable rotation. These are selection hints, not a binding of identity.
        ordered_search.sort(key=lambda record: -_source_relevance_hint(
            context.business, record["content"],
        ))
        contact_search_id = None
        is_maps_identity = (
            getattr(context.business, "provider", None) == "google_places"
            and isinstance(getattr(context.business, "source_key", None), str)
            and bool(context.business.source_key.strip())
        )
        known_website = (
            _research_url(getattr(context.business, "website", None))
            if is_maps_identity else None
        )
        if plan.get("purpose") == "contact" and known_website:
            known_parts = urlsplit(known_website)
            contact_candidates = [record for record in ordered_search if (
                (parts := urlsplit(record["url"])).scheme == known_parts.scheme
                and parts.netloc == known_parts.netloc
                and (
                    _contacts_from_original(record["content"])
                    or _is_contact_page_url(parts.path)
                )
            )]
            if contact_candidates:
                contact_search_id = contact_candidates[0]["evidence_id"]
                ordered_search = [
                    *contact_candidates,
                    *(record for record in ordered_search if record not in contact_candidates),
                ]
        anchor_extract_url = next((
            source.source_url for source in active_tavily
            if source.evidence_id == identity_anchor_id and source.type == "extract"
        ), None)
        # Search and Extract have distinct material identities. A verified
        # Extract URL may guide a new fetch only if Search returned it; its
        # identity assessment is never copied onto the new content.
        priority_search_id = next((
            record["evidence_id"] for record in ordered_search
            if record["evidence_id"] == identity_anchor_id
            or (anchor_extract_url is not None and record["url"] == anchor_extract_url)
        ), None)
        priority_search_ids = {priority_search_id} if priority_search_id is not None else set()
        if priority_search_ids:
            ordered_search = [
                *(record for record in ordered_search
                  if record["evidence_id"] in priority_search_ids),
                *(record for record in ordered_search
                  if record["evidence_id"] not in priority_search_ids),
            ]
        if contact_search_id is not None:
            ordered_search = [
                *(record for record in ordered_search
                  if record["evidence_id"] == contact_search_id),
                *(record for record in ordered_search
                  if record["evidence_id"] != contact_search_id),
            ]
        selected_search_ids = [
            record["evidence_id"] for record in ordered_search[:search_slots]
        ]
        selected_ids = [*fixed_ids, *selected_search_ids]
        selected_id_set = set(selected_ids)
        for record in search_records:
            record["selected"] = record["evidence_id"] in selected_id_set

        for record in search_records:
            if not record["existing"]:
                record["row"] = self._persist_evidence(
                    grant=grant, research=research, record=record,
                    provider_request_id=getattr(response, "request_id", None),
                )
        excluded_ids = [
            *expired_source_ids,
            *(evidence_id for evidence_id in active_tavily_ids
              if evidence_id not in selected_id_set),
            *(record["evidence_id"] for record in search_records
              if record["evidence_id"] not in selected_id_set),
        ]
        truncated = bool(excluded_ids or invalid_url_count)
        research.result_count = len(response.results[:10])
        research.search_credits = _usage_number(search_usage, "search_credits", "credits")
        research.provider_request_id = getattr(response, "request_id", None)
        research.query_context = {
            **research.query_context,
            "operations": {"search": {"provider": "tavily", "completed": True}},
            "provider_credits": {"search": research.search_credits, "extract": None, "total": None},
            "cost_usd": None,
            "cost_known": False,
            "invalid_url_count": invalid_url_count,
            "material_history": [self._history_record(record) for record in search_records],
            "source_selection": {
                "max_sources": MAX_DOSSIER_SOURCES,
                "selected_evidence_ids": list(dict.fromkeys(selected_ids)),
                "excluded_evidence_ids": list(dict.fromkeys(excluded_ids)),
                "truncated": truncated,
            },
            "evidence_hashes": [record["digest"] for record in search_records],
        }
        # Search results and their explicit bounded selection survive Extract timeout.
        self.db.commit()

        extract_usage: dict = {}
        extracted_count = 0
        active_extract_urls = {
            source.source_url for source in active_tavily if source.type == "extract"
        }
        extract_url = None
        if contact_search_id is not None:
            extract_url = next((
                record["url"] for record in ordered_search
                if record["evidence_id"] == contact_search_id
                and record["evidence_id"] in selected_id_set
            ), None)
        if extract_url is None and priority_search_ids:
            extract_url = next((
                record["url"] for record in ordered_search
                if record["evidence_id"] in priority_search_ids
                and record["evidence_id"] in selected_id_set
            ), None)
        if not novel_search:
            extract_url = extract_url or next((
                record["url"] for record in ordered_search
                if record["evidence_id"] in selected_id_set
                and record["url"] in active_extract_urls
            ), None)
        if extract_url is None:
            extract_url = next((record["url"] for record in ordered_search
                                if record["evidence_id"] in selected_id_set), None)
        extract_attempted = bool(extract_attempted and extract_url)
        extract_record = None
        if extract_attempted:
            if _research_url(extract_url) != extract_url:
                raise ResearchGrantConflict("extract URL failed safety validation")
            self._reserve(grant_id, lease, "extract")
            try:
                extracted, extract_usage = provider.extract([extract_url])
            except Exception:
                research.status = ProspectResearchStatus.FAILED
                research.completed_at = max(self.clock(), now)
                research.updated_at = research.completed_at
                research.error = "extract_provider_failed"
                self.db.commit()
                raise
            matching_extracts = [item for item in extracted if _research_url(item.url) == extract_url]
            if len(matching_extracts) > 1:
                raise ResearchGrantConflict("provider returned duplicate extracts")
            self._consume(grant_id, lease, "extract")
            if matching_extracts:
                item = matching_extracts[0]
                extracted_count = 1
                original_content = item.content or ""
                content = _contact_excerpt(original_content) if plan.get("purpose") == "contact" else original_content[:4000]
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                key = ("extract", extract_url, digest)
                previous = prior_materials.get(key) or batch_materials.get(key)
                record = {
                    "operation": "extract", "url": extract_url,
                    "content": content, "title": item.title, "digest": digest,
                    "key": key,
                    "evidence_id": previous["evidence_id"] if previous else self._evidence_id(
                        grant, research, "extract", extract_url, digest,
                    ),
                    "existing": previous is not None,
                    "selected": True,
                    "provider_request_id": getattr(response, "request_id", None),
                    "rank": 0,
                }
                extract_record = record
                batch_materials[key] = record
                if not record["existing"]:
                    record["row"] = self._persist_evidence(
                        grant=grant, research=research, record=record,
                        provider_request_id=getattr(response, "request_id", None),
                    )

        has_novel_material = bool(
            any(not record["existing"] for record in search_records)
            or extract_record is not None and not extract_record["existing"]
        )
        if has_novel_material:
            preferred_tavily_ids = [
                *([identity_anchor_id] if identity_anchor_id is not None else []),
                *([extract_record["evidence_id"]]
                  if extract_record is not None and not extract_record["existing"] else []),
                *(record["evidence_id"] for record in novel_search),
                *active_tavily_ids,
            ]
        else:
            preferred_tavily_ids = [
                *([identity_anchor_id] if identity_anchor_id is not None else []),
                *active_tavily_ids,
                *(record["evidence_id"] for record in search_records),
                *([extract_record["evidence_id"]] if extract_record is not None else []),
            ]
        relevance_by_id = {
            source.evidence_id: _source_relevance_hint(context.business, source.excerpt)
            for source in active_tavily
        }
        for record in [*search_records, *([extract_record] if extract_record is not None else [])]:
            relevance_by_id[record["evidence_id"]] = _source_relevance_hint(
                context.business, record["content"],
            )
        # Include relevant returned material even if it was seen previously;
        # do not let unrelated novelty evict it from the final six-source dossier.
        preferred_tavily_ids = list(dict.fromkeys([
            *preferred_tavily_ids, *(record["evidence_id"] for record in ordered_search),
        ]))
        preferred_tavily_ids.sort(key=lambda evidence_id: (
            evidence_id != identity_anchor_id, -relevance_by_id.get(evidence_id, 0),
        ))
        selected_ids = list(dict.fromkeys([
            *fixed_ids,
            *preferred_tavily_ids,
        ]))[:MAX_DOSSIER_SOURCES]
        selected_id_set = set(selected_ids)
        for record in search_records:
            record["selected"] = record["evidence_id"] in selected_id_set
            row = record.get("row")
            if row is not None:
                row.metadata_json = {
                    **(row.metadata_json if isinstance(row.metadata_json, dict) else {}),
                    "selected_for_dossier": record["selected"],
                }
        if extract_record is not None:
            extract_record["selected"] = extract_record["evidence_id"] in selected_id_set
            row = extract_record.get("row")
            if row is not None:
                row.metadata_json = {
                    **(row.metadata_json if isinstance(row.metadata_json, dict) else {}),
                    "selected_for_dossier": extract_record["selected"],
                }
        excluded_ids = list(dict.fromkeys([
            *expired_source_ids,
            *(evidence_id for evidence_id in active_tavily_ids
              if evidence_id not in selected_id_set),
            *(record["evidence_id"] for record in search_records
              if record["evidence_id"] not in selected_id_set),
            *([extract_record["evidence_id"]]
              if extract_record is not None
              and extract_record["evidence_id"] not in selected_id_set else []),
        ]))
        truncated = bool(excluded_ids or invalid_url_count)
        research.status = ProspectResearchStatus.COMPLETED
        research.completed_at = max(self.clock(), now)
        research.updated_at = research.completed_at
        research.extracted_count = extracted_count
        research.extract_credits = _usage_number(extract_usage, "extract_credits", "credits") if extract_usage else None
        reported_total = _usage_number(search_usage, "total_credits")
        if reported_total is None and research.search_credits is not None and (
            not extract_attempted or research.extract_credits is not None
        ):
            reported_total = research.search_credits + (research.extract_credits or 0)
        research.total_credits = reported_total
        current_context = research.query_context if isinstance(research.query_context, dict) else {}
        previous_operations = current_context.get("operations")
        research.query_context = {
            **current_context,
            "operations": {
                **(previous_operations if isinstance(previous_operations, dict) else {}),
                "extract": {
                    "provider": "tavily", "completed": extract_attempted,
                    "requested_url": extract_url if extract_attempted else None,
                },
            },
            "material_history": [
                *(self._history_record(record) for record in search_records),
                *([self._history_record(extract_record)] if extract_record is not None else []),
            ],
            "evidence_hashes": [
                *(record["digest"] for record in search_records),
                *([extract_record["digest"]] if extract_record is not None else []),
            ],
            "provider_credits": {
                "search": research.search_credits, "extract": research.extract_credits,
                "total": research.total_credits,
                "total_known": research.total_credits is not None,
            },
            "source_selection": {
                "max_sources": MAX_DOSSIER_SOURCES,
                "selected_evidence_ids": selected_ids,
                "excluded_evidence_ids": excluded_ids,
                "truncated": truncated,
            },
        }
        self.db.commit()

    def _prior_material_index(self, context, *, workspace_id, lead_id, freshness_cutoff):
        index: dict[tuple[str, str, str], dict] = {}
        cursor_id = getattr(context.dossier, "research_id", None)
        seen: set[UUID] = set()
        snapshots = []
        while cursor_id is not None:
            if cursor_id in seen or len(snapshots) >= 20:
                raise ResearchGrantConflict("research ancestry is cyclic or exceeds the validated depth")
            seen.add(cursor_id)
            snapshot = self.db.execute(select(ProspectResearch).where(
                ProspectResearch.workspace_id == workspace_id,
                ProspectResearch.lead_id == lead_id,
                ProspectResearch.id == cursor_id,
                ProspectResearch.status == ProspectResearchStatus.COMPLETED,
            )).scalar_one_or_none()
            if snapshot is None:
                raise ResearchGrantConflict("current research ancestry is unavailable")
            snapshots.append(snapshot)
            cursor_id = snapshot.refresh_of_id
        for snapshot in snapshots:
            rows = self.db.execute(select(ResearchEvidence).where(
                ResearchEvidence.workspace_id == workspace_id,
                ResearchEvidence.lead_id == lead_id,
                ResearchEvidence.research_id == snapshot.id,
                ResearchEvidence.provider == "tavily",
            )).scalars().all()
            for row in rows:
                metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
                material_digest = metadata.get("material_digest")
                if (
                    row.operation in {"search", "extract"}
                    and row.retrieved_at >= freshness_cutoff
                    and metadata.get("provider") == "tavily"
                    and metadata.get("operation") == row.operation
                    and isinstance(material_digest, str)
                    and material_digest == hashlib.sha256(row.excerpt.encode("utf-8")).hexdigest()
                ):
                    index[(row.operation, row.source_url, material_digest)] = {
                        "evidence_id": row.evidence_id,
                    }
        return index

    @staticmethod
    def _evidence_id(grant, research, operation, url, material_digest):
        return hashlib.sha256(
            f"{grant.workspace_id}:{grant.lead_id}:{research.id}:{operation}:{url}:{material_digest}".encode()
        ).hexdigest()

    def _persist_evidence(self, *, grant, research, record, provider_request_id):
        row = ResearchEvidence(
            id=uuid4(), evidence_id=record["evidence_id"],
            workspace_id=grant.workspace_id, research_id=research.id,
            lead_id=grant.lead_id, provider="tavily", operation=record["operation"],
            source_type="web_page", source_url=record["url"], source_title=record["title"],
            excerpt=record["content"], retrieved_at=self.clock(),
            metadata_json={
                "provider": "tavily", "operation": record["operation"],
                "grant_id": str(grant.id), "plan_hash": grant.plan_hash,
                "material_digest": record["digest"],
                "provider_request_id": provider_request_id,
                "selected_for_dossier": record["selected"],
                "structured_facts": {"contacts": _contacts_from_original(record["content"])},
                "truncated": len(record["content"]) >= 4000,
            },
        )
        self.db.add(row)
        return row

    @staticmethod
    def _history_record(record):
        return {
            "operation": record["operation"], "url": record["url"],
            "evidence_id": record["evidence_id"],
            "material_digest": record["digest"], "rank": record["rank"],
            "selected": record["selected"],
        }

    def _evaluation_service(self, *, jev_factory=None):
        if self._evaluation_service_factory is not None:
            return self._evaluation_service_factory(jev_factory=jev_factory)
        return LeadFitEvaluationService(
            self.db, policy_service=self._policy_service,
            jev_factory=jev_factory or self._jev_factory,
        )

    def _budgeted_jev_factory(self, grant_id: UUID, lease: str):
        def build():
            from app.core.config import get_settings
            from app.integrations.jev import JevDecisionClient

            settings = get_settings()
            delegate = self._jev_factory() if self._jev_factory else JevDecisionClient(
                api_key=settings.openrouter_api_key,
                enabled=settings.jev_enabled,
                timeout_seconds=settings.jev_timeout_seconds,
                max_request_bytes=settings.jev_max_request_bytes,
                authorized_models=settings.jev_authorized_models,
            )

            class BudgetedClient:
                def evaluate(_self, state, questions):
                    self._reserve(grant_id, lease, "jev")
                    response = delegate.evaluate(state, questions)
                    self._consume(grant_id, lease, "jev")
                    return response

            return BudgetedClient()
        return build

    def _reserve(self, grant_id: UUID, lease: str, operation: str) -> None:
        grant = self._grant(grant_id, lock=True)
        if grant is None or grant.status is not LeadFitResearchGrantStatus.RUNNING or grant.lease_owner != lease:
            raise ResearchGrantConflict("research lease lost")
        reserved_name = f"reserved_{operation}"
        maximum = getattr(grant, f"max_{operation}")
        if getattr(grant, reserved_name) >= maximum:
            raise ResearchGrantConflict("research grant operation limit exhausted")
        setattr(grant, reserved_name, getattr(grant, reserved_name) + 1)
        if grant.dispatched_at is None:
            grant.dispatched_at = self.clock()
        self.db.commit()

    def _consume(self, grant_id: UUID, lease: str, operation: str) -> None:
        grant = self._grant(grant_id, lock=True)
        if grant is None or grant.lease_owner != lease:
            raise ResearchGrantConflict("research lease lost after provider response")
        name = f"consumed_{operation}"
        setattr(grant, name, getattr(grant, name) + 1)
        self.db.commit()

    def _grant(self, grant_id: UUID, *, lock=False):
        statement = select(LeadFitResearchGrant).where(LeadFitResearchGrant.id == grant_id)
        if lock:
            statement = statement.with_for_update()
        return self.db.execute(statement).scalar_one_or_none()

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        if isinstance(exc, ResearchGrantConflict):
            return "research_conflict"
        if type(exc).__name__ in {"TavilyProviderError", "JevClientError"}:
            return "provider_failed"
        return "research_execution_failed"


def _usage_number(usage: dict, *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if type(value) in (int, float) and value >= 0:
            return int(value)
    return None


def _contacts_from_original(content: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(channel: str, value: str) -> None:
        contact = (channel, value)
        if contact not in seen and len(found) < 10:
            seen.add(contact)
            found.append({"channel": channel, "value": value})

    for email in _EMAIL.findall(content):
        add("email", email)
    for link in _WHATSAPP.findall(content):
        add("whatsapp", link)
    for match in _TEL_PHONE.finditer(content):
        raw = match.group(1)
        digits = re.sub(r"\D", "", raw)
        if 7 <= len(digits) <= 15:
            phone = ("+" if raw.lstrip().startswith("+") else "") + digits
            add("phone", phone)
    return found


def _contact_excerpt(content: str) -> str:
    """Choose one bounded contiguous source window that retains explicit contacts."""
    if len(content) <= 4000:
        return content
    markers = [match.start() for pattern in (_EMAIL, _WHATSAPP, _TEL_PHONE)
               for match in pattern.finditer(content)]
    if not markers:
        return content[:4000]
    start = max(0, min(markers) - 2000)
    start = min(start, len(content) - 4000)
    return content[start:start + 4000]


def _is_contact_page_url(path: str) -> bool:
    return any(
        segment.casefold() in {
            "contact", "contacts", "contacto", "contactos", "contactanos", "contactenos",
        }
        for segment in path.strip("/").split("/")
    )
