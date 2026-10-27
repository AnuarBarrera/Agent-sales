from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings, get_settings
from app.integrations.tavily import TavilyProvider
from app.modules.lead_fit.contracts import canonical_sha256
from app.modules.lead_fit.models import LeadFitPolicy, LeadFitPolicyStatus
from app.modules.lead_fit.service import LeadFitPolicyService
from app.modules.leads.models import Business, Lead
from app.modules.prospect_research.models import (
    ProspectResearch,
    ProspectResearchStatus,
    ResearchEvidence,
)
from app.modules.prospect_research.service import MAX_EXCERPT_LENGTH, _research_url, _usage_int
from app.modules.prospection.models import ProspectionRun, ProspectionRunStatus, RunLead

_ENRICHMENT_BUDGET = {
    "version": 1,
    "max_search_calls": 1,
    "max_extract_calls": 1,
    "max_sources": 3,
}


@dataclass(frozen=True)
class _Admission:
    workspace_id: UUID
    lead_id: UUID
    run_id: UUID
    research_id: UUID
    policy_id: UUID
    policy_digest: str
    plan_digest: str
    maps_snapshot_id: UUID
    query: str
    business_name: str
    address: str | None
    website_url: str | None
    place_id: str | None


@dataclass(frozen=True)
class _Source:
    source_type: str
    operation: str
    source_url: str
    original_url: str
    title: str | None
    excerpt: str
    relevance_score: float | None
    request_id: str | None
    material_digest: str
    truncated: bool


class InitialEnrichmentService:
    """One durable, bounded Tavily enrichment per lead in a Maps run."""

    def __init__(
        self,
        db: Session,
        provider_factory: Callable[[Settings], TavilyProvider] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.provider_factory = provider_factory or TavilyProvider

    def enrich(
        self, workspace_id: UUID, lead_id: UUID, run_id: UUID, policy_id: UUID
    ) -> ProspectResearch:
        admission, research = self._admit(workspace_id, lead_id, run_id, policy_id)
        if admission is None:
            return research

        search_reserved = self._reserve(admission, "search")
        if not search_reserved:
            return self._failed(admission, "authority_changed", [])

        provider = None
        sources: list[_Source] = []
        search_request_id = None
        extract_request_id = None
        search_credits = None
        extract_credits = None
        search_result_count = 0
        extracted_count = 0
        try:
            provider = self.provider_factory(self.settings)
            try:
                response = provider.search(admission.query, max_results=3, search_depth="basic")
            except Exception:
                return self._failed(admission, "tavily_search_failed", [])

            search_request_id = _safe_request_id(getattr(response, "request_id", None))
            usage = getattr(response, "usage", {})
            search_credits = _usage_int(usage if isinstance(usage, dict) else {}, "search_credits", "credits")
            results = getattr(response, "results", [])
            if not isinstance(results, list):
                return self._failed(admission, "tavily_search_invalid_response", [])
            search_result_count = len(results)
            for result in results[:3]:
                source = _search_source(result, search_request_id)
                if source is not None:
                    sources.append(source)
            if not self._record_dispatch(admission, search_request_id, search_credits, None, search_result_count, 0):
                return self._failed(admission, "authority_changed", [])

            extract_url = _research_url(admission.website_url)
            if extract_url:
                if not self._reserve(admission, "extract"):
                    return self._failed(admission, "authority_changed", sources)
                try:
                    extracts, extract_usage = provider.extract([extract_url])
                except Exception:
                    return self._failed(admission, "tavily_extract_failed", sources)
                if not isinstance(extracts, list) or not isinstance(extract_usage, dict) or len(extracts) > 1:
                    return self._failed(admission, "tavily_extract_invalid_response", sources)
                extract_request_id = _safe_request_id(extract_usage.get("request_id"))
                extract_credits = _usage_int(extract_usage, "extract_credits", "credits")
                selected = _selected_extract(extracts, extract_url, admission.website_url, extract_request_id)
                if extracts and selected is None:
                    return self._failed(admission, "tavily_extract_invalid_response", sources)
                if selected is not None:
                    sources.insert(0, selected)
                    extracted_count = 1
                if not self._record_dispatch(
                    admission, search_request_id, search_credits, extract_request_id,
                    search_result_count, extracted_count,
                ):
                    return self._failed(admission, "authority_changed", [])

            return self._complete(
                admission, sources[:3], search_request_id, extract_request_id,
                search_credits, extract_credits, search_result_count, extracted_count,
            )
        except Exception:
            return self._failed(admission, "initial_enrichment_failed", sources)
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def _admit(
        self, workspace_id: UUID, lead_id: UUID, run_id: UUID, policy_id: UUID
    ) -> tuple[_Admission | None, ProspectResearch]:
        try:
            snapshot = self._validated_snapshot(workspace_id, lead_id, run_id, policy_id, lock_run=True)
        except Exception:
            self.db.rollback()
            raise
        existing_rows = self.db.execute(select(ProspectResearch).where(
            ProspectResearch.workspace_id == workspace_id,
            ProspectResearch.lead_id == lead_id,
            ProspectResearch.operation == "initial_enrichment",
        )).scalars().all()
        for existing in existing_rows:
            context = existing.query_context if isinstance(existing.query_context, dict) else {}
            if context.get("run_id") == str(run_id):
                self.db.commit()
                return None, existing

        query_context = {
            "run_id": str(run_id),
            "plan_digest": snapshot.plan_digest,
            "policy_digest": snapshot.policy_digest,
            "source_context": {
                "business_name": snapshot.business_name,
                "address": snapshot.address,
                "maps_website_url": snapshot.website_url,
                "place_id": snapshot.place_id,
            },
            "counters": {"search_calls": 0, "extract_calls": 0, "sources": 0},
            "provider_request_ids": {"search": None, "extract": None},
        }
        research = ProspectResearch(
            workspace_id=workspace_id,
            lead_id=lead_id,
            status=ProspectResearchStatus.PENDING,
            provider="tavily",
            operation="initial_enrichment",
            query_summary=snapshot.query,
            query_context=query_context,
            refresh_of_id=snapshot.maps_snapshot_id,
        )
        self.db.add(research)
        self.db.flush()
        admission = _Admission(
            workspace_id=workspace_id, lead_id=lead_id, run_id=run_id,
            research_id=research.id, policy_id=snapshot.policy_id,
            policy_digest=snapshot.policy_digest, plan_digest=snapshot.plan_digest,
            query=snapshot.query, business_name=snapshot.business_name,
            address=snapshot.address, website_url=snapshot.website_url, place_id=snapshot.place_id,
            maps_snapshot_id=snapshot.maps_snapshot_id,
        )
        self.db.commit()
        return admission, research

    def _validated_snapshot(
        self, workspace_id: UUID, lead_id: UUID, run_id: UUID, policy_id: UUID, *, lock_run: bool
    ) -> _Admission:
        statement = select(ProspectionRun).where(
            ProspectionRun.workspace_id == workspace_id,
            ProspectionRun.id == run_id,
        ).execution_options(populate_existing=True)
        if lock_run:
            statement = statement.with_for_update()
        run = self.db.execute(statement).scalar_one_or_none()
        if run is None or run.status is not ProspectionRunStatus.ENRICHING:
            raise ValueError("initial_enrichment_run_unavailable")
        if not _valid_budget(run.search_plan_snapshot):
            raise ValueError("initial_enrichment_plan_invalid")
        if run.fit_policy_id != policy_id:
            raise ValueError("initial_enrichment_policy_mismatch")

        plan = run.search_plan_snapshot
        candidate_limit = plan.get("candidate_limit")
        if (
            plan.get("strategy") != "maps"
            or type(candidate_limit) is not int
            or not 1 <= candidate_limit <= 3
        ):
            raise ValueError("initial_enrichment_plan_invalid")
        plan_material = {key: value for key, value in plan.items() if key != "plan_digest"}
        plan_digest = canonical_sha256(plan_material)
        supplied_plan_digest = plan.get("plan_digest")
        if not isinstance(supplied_plan_digest, str) or supplied_plan_digest != plan_digest:
            raise ValueError("initial_enrichment_plan_invalid")

        policy = self.db.execute(select(LeadFitPolicy).where(
            LeadFitPolicy.workspace_id == workspace_id,
            LeadFitPolicy.id == policy_id,
            LeadFitPolicy.status == LeadFitPolicyStatus.APPROVED,
        )).scalar_one_or_none()
        if policy is None:
            raise ValueError("initial_enrichment_policy_unavailable")
        if plan.get("policy_id") != str(policy.id):
            raise ValueError("initial_enrichment_policy_mismatch")
        if plan.get("policy_digest") != policy.policy_digest:
            raise ValueError("initial_enrichment_policy_mismatch")
        current = LeadFitPolicyService(self.db).current(workspace_id, policy.playbook_id)
        if current is None or current.id != policy.id or current.policy_digest != policy.policy_digest:
            raise ValueError("initial_enrichment_policy_stale")

        lead = self.db.execute(select(Lead).options(joinedload(Lead.business)).where(
            Lead.workspace_id == workspace_id,
            Lead.id == lead_id,
        )).scalar_one_or_none()
        if lead is None:
            raise ValueError("initial_enrichment_lead_unavailable")
        business = self.db.execute(select(Business).where(
            Business.workspace_id == workspace_id,
            Business.id == lead.business_id,
        )).scalar_one_or_none()
        if business is None or lead.business_id != business.id:
            raise ValueError("initial_enrichment_business_unavailable")
        membership = self.db.execute(select(RunLead).where(
            RunLead.workspace_id == workspace_id,
            RunLead.run_id == run_id,
            RunLead.lead_id == lead_id,
        )).scalar_one_or_none()
        if membership is None:
            raise ValueError("initial_enrichment_run_lead_mismatch")

        maps_key = membership.source_key or membership.place_id or business.source_key or business.place_id
        snapshots = self.db.execute(select(ProspectResearch).where(
            ProspectResearch.workspace_id == workspace_id,
            ProspectResearch.lead_id == lead_id,
            ProspectResearch.provider == "google_places",
            ProspectResearch.operation == "places_snapshot",
            ProspectResearch.status == ProspectResearchStatus.COMPLETED,
        ).order_by(ProspectResearch.created_at.desc())).scalars().all()
        maps_snapshot = None
        maps_facts = None
        for candidate_snapshot in snapshots:
            candidate_context = candidate_snapshot.query_context if isinstance(candidate_snapshot.query_context, dict) else {}
            if candidate_context.get("source_key") != maps_key:
                continue
            original_evidence = self.db.execute(select(ResearchEvidence).where(
                ResearchEvidence.workspace_id == workspace_id,
                ResearchEvidence.lead_id == lead_id,
                ResearchEvidence.research_id == candidate_snapshot.id,
                ResearchEvidence.provider == "google_places",
                ResearchEvidence.operation == "places_snapshot",
                ResearchEvidence.source_type == "maps_place",
            )).scalars().all()
            for evidence in original_evidence:
                metadata = evidence.metadata_json if isinstance(evidence.metadata_json, dict) else {}
                facts = metadata.get("structured_facts")
                if isinstance(facts, dict) and facts.get("place_id") == maps_key:
                    maps_snapshot, maps_facts = candidate_snapshot, facts
                    break
            if maps_snapshot is not None:
                break
        if maps_snapshot is None or not isinstance(maps_facts, dict):
            raise ValueError("initial_enrichment_maps_snapshot_unavailable")

        query = " ".join(
            str(value).strip()
            for value in (maps_facts.get("name"), maps_facts.get("address"), lead.giro, lead.zona)
            if isinstance(value, str) and value.strip()
        )[:500]
        if not query:
            query = business.name[:500]
        return _Admission(
            workspace_id=workspace_id, lead_id=lead_id, run_id=run_id,
            research_id=UUID(int=0), policy_id=policy.id, policy_digest=policy.policy_digest,
            plan_digest=plan_digest, maps_snapshot_id=maps_snapshot.id, query=query,
            business_name=maps_facts.get("name") if isinstance(maps_facts.get("name"), str) else business.name,
            address=maps_facts.get("address") if isinstance(maps_facts.get("address"), str) else business.address,
            website_url=maps_facts.get("website_url") if isinstance(maps_facts.get("website_url"), str) else None,
            place_id=maps_key,
        )

    def _reserve(self, admission: _Admission, operation: str) -> bool:
        try:
            current = self._validated_snapshot(
                admission.workspace_id, admission.lead_id, admission.run_id,
                admission.policy_id, lock_run=True,
            )
            if not _same_authority(current, admission):
                self.db.rollback()
                return False
        except ValueError:
            self.db.rollback()
            return False
        research = self._research(admission)
        if research is None or research.status not in {
            ProspectResearchStatus.PENDING, ProspectResearchStatus.RUNNING,
        }:
            self.db.rollback()
            return False
        context = _context(research)
        counters = dict(context.get("counters", {}))
        counter = f"{operation}_calls"
        if counters.get(counter) != 0:
            self.db.rollback()
            return False
        counters[counter] = 1
        context["counters"] = counters
        research.query_context = context
        research.status = ProspectResearchStatus.RUNNING
        if research.started_at is None:
            research.started_at = datetime.now(UTC)
        self.db.commit()
        return True

    def _record_dispatch(
        self, admission: _Admission, search_request_id: str | None, search_credits: int | None,
        extract_request_id: str | None, result_count: int, extracted_count: int,
    ) -> bool:
        if not self._still_current(admission):
            return False
        research = self._research(admission)
        if research is None:
            raise ValueError("initial_enrichment_record_unavailable")
        context = _context(research)
        context["provider_request_ids"] = {"search": search_request_id, "extract": extract_request_id}
        research.query_context = context
        research.provider_request_id = extract_request_id or search_request_id
        research.search_credits = search_credits
        research.extract_credits = None
        research.result_count = result_count
        research.extracted_count = extracted_count
        self.db.commit()
        return True

    def _complete(
        self, admission: _Admission, sources: list[_Source], search_request_id: str | None,
        extract_request_id: str | None, search_credits: int | None, extract_credits: int | None,
        result_count: int, extracted_count: int,
    ) -> ProspectResearch:
        valid = self._still_current(admission)
        research = self._research(admission)
        if research is None:
            raise ValueError("initial_enrichment_record_unavailable")
        context = _context(research)
        context["provider_request_ids"] = {"search": search_request_id, "extract": extract_request_id}
        counters = dict(context.get("counters", {}))
        counters["sources"] = len(sources) if valid else 0
        context["counters"] = counters
        research.query_context = context
        research.provider_request_id = extract_request_id or search_request_id
        research.search_credits = search_credits
        research.extract_credits = extract_credits
        research.total_credits = _sum_credits(search_credits, extract_credits)
        research.result_count = result_count
        research.extracted_count = extracted_count
        research.completed_at = datetime.now(UTC)
        if not valid:
            research.status = ProspectResearchStatus.FAILED
            research.error = "initial_enrichment_authority_changed"
        else:
            self._persist_sources(admission, research, sources[:3])
            research.status = ProspectResearchStatus.COMPLETED
            research.error = None
        self.db.commit()
        return research

    def _failed(self, admission: _Admission, error: str, sources: list[_Source]) -> ProspectResearch:
        valid = self._still_current(admission)
        research = self._research(admission)
        if research is None:
            raise ValueError("initial_enrichment_record_unavailable")
        context = _context(research)
        ids = context.get("provider_request_ids", {})
        if valid and sources:
            self._persist_sources(admission, research, sources[:3])
        counters = dict(context.get("counters", {}))
        counters["sources"] = len(sources[:3]) if valid else 0
        context["counters"] = counters
        research.query_context = context
        research.status = ProspectResearchStatus.FAILED
        research.error = error if valid else "initial_enrichment_authority_changed"
        research.provider_request_id = ids.get("extract") or ids.get("search")
        research.completed_at = datetime.now(UTC)
        self.db.commit()
        return research

    def _still_current(self, admission: _Admission) -> bool:
        try:
            snapshot = self._validated_snapshot(
                admission.workspace_id, admission.lead_id, admission.run_id,
                admission.policy_id, lock_run=True,
            )
            return _same_authority(snapshot, admission)
        except ValueError:
            self.db.rollback()
            return False

    def _research(self, admission: _Admission) -> ProspectResearch | None:
        return self.db.execute(select(ProspectResearch).where(
            ProspectResearch.workspace_id == admission.workspace_id,
            ProspectResearch.lead_id == admission.lead_id,
            ProspectResearch.id == admission.research_id,
            ProspectResearch.operation == "initial_enrichment",
        ).with_for_update()).scalar_one_or_none()

    def _persist_sources(
        self, admission: _Admission, research: ProspectResearch, sources: list[_Source]
    ) -> None:
        for index, source in enumerate(sources[:3]):
            self.db.add(ResearchEvidence(
                evidence_id=f"tavily:{research.id}:initial:{index}",
                workspace_id=admission.workspace_id,
                research_id=research.id,
                lead_id=admission.lead_id,
                provider="tavily",
                operation="initial_enrichment",
                source_type=source.source_type,
                source_url=source.source_url,
                source_title=source.title,
                excerpt=source.excerpt[:MAX_EXCERPT_LENGTH],
                retrieved_at=datetime.now(UTC),
                relevance_score=source.relevance_score,
                metadata_json={
                    "provider_request_id": source.request_id,
                    "original_url": source.original_url,
                    "source_type": source.source_type,
                    "provider": "tavily",
                    "operation": "initial_enrichment",
                    "source_operation": source.operation,
                    "material_digest": source.material_digest,
                    "truncated": source.truncated,
                },
            ))
        context = _context(research)
        counters = dict(context.get("counters", {}))
        counters["sources"] = len(sources[:3])
        context["counters"] = counters
        research.query_context = context


def _valid_budget(plan: Any) -> bool:
    if not isinstance(plan, dict):
        return False
    budget = plan.get("enrichment")
    return (
        isinstance(budget, dict)
        and set(budget) == set(_ENRICHMENT_BUDGET)
        and all(type(budget[key]) is int for key in _ENRICHMENT_BUDGET)
        and budget == _ENRICHMENT_BUDGET
    )


def _same_authority(current: _Admission, admitted: _Admission) -> bool:
    return (
        current.policy_id == admitted.policy_id
        and current.policy_digest == admitted.policy_digest
        and current.plan_digest == admitted.plan_digest
        and current.maps_snapshot_id == admitted.maps_snapshot_id
    )


def _context(research: ProspectResearch) -> dict[str, Any]:
    return dict(research.query_context) if isinstance(research.query_context, dict) else {}


def _safe_request_id(value: Any) -> str | None:
    return value[:255] if isinstance(value, str) and value else None


def _search_source(result: Any, request_id: str | None) -> _Source | None:
    original_url = getattr(result, "url", None)
    source_url = _research_url(original_url)
    content = getattr(result, "content", None)
    if not source_url or not isinstance(content, str) or not content:
        return None
    title = getattr(result, "title", None)
    score = getattr(result, "score", None)
    material = content.encode("utf-8")
    return _Source(
        source_type="search_result", operation="search", source_url=source_url,
        original_url=original_url, title=title if isinstance(title, str) else None,
        excerpt=content[:MAX_EXCERPT_LENGTH],
        relevance_score=float(score) if isinstance(score, int | float) and not isinstance(score, bool) else None,
        request_id=request_id,
        material_digest=hashlib.sha256(material).hexdigest(),
        truncated=len(content) > MAX_EXCERPT_LENGTH,
    )


def _selected_extract(
    extracts: list[Any], requested_url: str, original_url: str, request_id: str | None
) -> _Source | None:
    selected = []
    for result in extracts:
        returned_url = getattr(result, "url", None)
        content = getattr(result, "content", None)
        if _research_url(returned_url) != requested_url or not isinstance(content, str) or not content:
            continue
        selected.append(result)
    if len(selected) != 1:
        return None
    result = selected[0]
    title = getattr(result, "title", None)
    material = result.content.encode("utf-8")
    return _Source(
        source_type="selected_extract", operation="extract", source_url=requested_url,
        original_url=original_url, title=title if isinstance(title, str) else None,
        excerpt=result.content[:MAX_EXCERPT_LENGTH], relevance_score=None, request_id=request_id,
        material_digest=hashlib.sha256(material).hexdigest(),
        truncated=len(result.content) > MAX_EXCERPT_LENGTH,
    )


def _sum_credits(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None
