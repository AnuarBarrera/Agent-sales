"""Tenant and provenance checked loader for the evaluation dossier."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.modules.lead_fit.contracts import ContactPoint, EvidenceItem, LeadDossier, canonical_sha256
from app.modules.lead_fit.models import (
    LeadFitEvaluation,
    LeadFitPolicy,
    LeadFitResearchGrant,
)
from app.modules.leads.models import Business, Lead
from app.modules.prospect_research.models import (
    ProspectResearch,
    ProspectResearchStatus,
    ResearchEvidence,
)
from app.modules.prospect_research.service import _research_url


class DossierError(ValueError):
    """The stored lead or evidence cannot safely support an evaluation."""


EVIDENCE_EXCERPT_MAX_CHARS = 4000
SOURCE_SNAPSHOT_DIGEST_VERSION = 2


def load_server_dossier(
    db,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    policy: LeadFitPolicy,
    now: datetime | None = None,
) -> LeadDossier:
    """Load a bounded, tenant-bound dossier and derive its digest from facts."""
    if policy.workspace_id != workspace_id:
        raise DossierError("policy workspace mismatch")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise DossierError("current time must be timezone-aware")

    lead = db.execute(
        select(Lead).where(Lead.workspace_id == workspace_id, Lead.id == lead_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if lead is None:
        raise DossierError("lead not found")
    _validate_timestamp(getattr(lead, "created_at", None), current_time, "lead")
    business = db.execute(
        select(Business).where(
            Business.workspace_id == workspace_id,
            Business.id == lead.business_id,
        ).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if business is None:
        raise DossierError("business not found")
    _validate_timestamp(getattr(business, "updated_at", None), current_time, "business")

    research = db.execute(
        select(ProspectResearch).where(
            ProspectResearch.workspace_id == workspace_id,
            ProspectResearch.lead_id == lead_id,
            ProspectResearch.status == ProspectResearchStatus.COMPLETED,
        ).order_by(ProspectResearch.created_at.desc()).limit(1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    rows: list[tuple[ProspectResearch, ResearchEvidence]] = []
    if research is not None:
        if research.workspace_id != workspace_id or research.lead_id != lead_id:
            raise DossierError("research binding mismatch")
        for field in ("requested_at", "started_at", "completed_at", "created_at", "updated_at"):
            value = getattr(research, field, None)
            if value is not None:
                _validate_timestamp(value, current_time, "research")
        snapshots: list[ProspectResearch] = []
        seen_snapshots: set[UUID] = set()
        cursor = research
        while cursor is not None:
            if cursor.id in seen_snapshots or len(snapshots) >= 20:
                raise DossierError("research snapshot ancestry is cyclic or too deep")
            if cursor.workspace_id != workspace_id or cursor.lead_id != lead_id:
                raise DossierError("research ancestry binding mismatch")
            seen_snapshots.add(cursor.id)
            snapshots.append(cursor)
            parent_id = cursor.refresh_of_id
            if parent_id is None:
                break
            cursor = db.execute(select(ProspectResearch).where(
                ProspectResearch.workspace_id == workspace_id,
                ProspectResearch.lead_id == lead_id,
                ProspectResearch.id == parent_id,
                ProspectResearch.status == ProspectResearchStatus.COMPLETED,
            )).scalar_one_or_none()
            if cursor is None:
                raise DossierError("research ancestry snapshot is missing")
        for snapshot in reversed(snapshots):
            for row in db.execute(
                select(ResearchEvidence).where(
                    ResearchEvidence.research_id == snapshot.id,
                ).order_by(ResearchEvidence.retrieved_at.asc(), ResearchEvidence.id.asc())
                .execution_options(populate_existing=True)
            ).scalars().all():
                rows.append((snapshot, row))

    evidence: list[EvidenceItem] = []
    facts_by_evidence_id: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    grants: dict[UUID, LeadFitResearchGrant] = {}
    evaluations: dict[UUID, LeadFitEvaluation] = {}
    container_context = research.query_context if research and isinstance(research.query_context, dict) else {}
    for source_research, row in rows:
        if (
            row.workspace_id != workspace_id
            or row.lead_id != lead_id
            or row.research_id != source_research.id
        ):
            raise DossierError("foreign evidence binding")
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        ordinary_provenance = (
            row.provider == source_research.provider
            and row.operation == source_research.operation
        )
        incremental_provenance = False
        if source_research.provider == "lead_fit_research":
            incremental_provenance = _validate_incremental_provenance(
                db, workspace_id=workspace_id, lead_id=lead_id,
                research=source_research, row=row, metadata=metadata,
                grant_cache=grants, evaluation_cache=evaluations,
            )
        if not (ordinary_provenance or incremental_provenance):
            raise DossierError("evidence provenance does not match its research snapshot")
        if row.evidence_id in seen:
            raise DossierError("duplicate evidence id")
        seen.add(row.evidence_id)
        retrieved_at = row.retrieved_at
        _validate_timestamp(retrieved_at, current_time, "evidence")
        material_digest = metadata.get("material_digest")
        if incremental_provenance and (
            not isinstance(material_digest, str)
            or material_digest != hashlib.sha256(row.excerpt.encode("utf-8")).hexdigest()
        ):
            raise DossierError("incremental evidence material digest does not match original excerpt bytes")
        facts = metadata.get("structured_facts", {})
        if not isinstance(facts, dict):
            facts = {}
        matched = (
            row.provider == "google_places"
            and business.provider == "google_places"
            and business.source_key is not None
            and business.source_key == facts.get("place_id")
            and row.operation == "places_snapshot"
        )
        source_truncated = (
            metadata.get("truncated") is True or len(row.excerpt) > EVIDENCE_EXCERPT_MAX_CHARS
        )
        bounded_facts = _bounded_facts(facts)
        facts_by_evidence_id[row.evidence_id] = bounded_facts
        evidence.append(EvidenceItem(
            evidence_id=row.evidence_id,
            workspace_id=workspace_id,
            lead_id=lead_id,
            research_id=row.research_id,
            provider=row.provider,
            type=row.operation,
            source_url=row.source_url,
            retrieved_at=retrieved_at,
            excerpt=row.excerpt[:EVIDENCE_EXCERPT_MAX_CHARS],
            truncated=source_truncated,
            structured_facts=bounded_facts,
            content_hash=canonical_sha256({
                "provider": row.provider,
                "operation": row.operation,
                "url": row.source_url,
                "excerpt": row.excerpt,
                "retrieved_at": retrieved_at.isoformat(),
                "facts": bounded_facts,
            }),
            original_material_digest=(
                metadata.get("material_digest")
                if isinstance(metadata.get("material_digest"), str)
                and len(metadata["material_digest"]) == 64
                else None
            ),
            subject_status="matched" if matched else "unresolved",
            association_method="exact_google_place_id" if matched else "unresolved",
            association_confidence=1.0 if matched else None,
        ))
    selection = container_context.get("source_selection")
    selection_truncated = False
    if selection is not None:
        if not isinstance(selection, dict):
            raise DossierError("source selection metadata must be an object")
        selected_ids = selection.get("selected_evidence_ids")
        excluded_ids = selection.get("excluded_evidence_ids", [])
        if (
            not isinstance(selected_ids, list)
            or len(selected_ids) > 6
            or any(not isinstance(value, str) or not value for value in selected_ids)
            or len(selected_ids) != len(set(selected_ids))
            or not isinstance(excluded_ids, list)
            or any(not isinstance(value, str) or not value for value in excluded_ids)
        ):
            raise DossierError("source selection metadata is invalid or exceeds the six-source cap")
        available_ids = set(seen)
        if not set(selected_ids) <= available_ids:
            raise DossierError("source selection references evidence outside snapshot ancestry")
        selected_set = set(selected_ids)
        evidence = [item for item in evidence if item.evidence_id in selected_set]
        facts_by_evidence_id = {
            key: value for key, value in facts_by_evidence_id.items() if key in selected_set
        }
        selection_truncated = selection.get("truncated") is True
        if selection_truncated:
            evidence = [item.model_copy(update={"truncated": True}) for item in evidence]
    if len(evidence) > 6:
        raise DossierError("too many evidence rows; explicit bounded selection is required")

    primary_facts = _business_facts(business)

    contacts: list[ContactPoint] = []
    for source in evidence:
        source_facts = facts_by_evidence_id[source.evidence_id]
        for item in source_facts.get("contacts", []):
            channel, value = item["channel"], item["value"]
            contacts.append(ContactPoint(
                channel=channel,
                value=value,
                evidence_ids=(source.evidence_id,),
                subject_status=source.subject_status,
                valid_format=_valid_contact_format(channel, value),
            ))
    material = {
        "digest_version": SOURCE_SNAPSHOT_DIGEST_VERSION,
        "workspace_id": str(workspace_id),
        "lead_id": str(lead_id),
        "business": {
            "id": str(business.id), "provider": business.provider,
            "source_key": business.source_key, "facts": primary_facts,
        },
        "evidence": [
            {
                "id": item.evidence_id, "provider": item.provider, "type": item.type,
                "url": item.source_url,
                "hash": item.content_hash, "retrieved_at": item.retrieved_at.isoformat(),
                "visible_excerpt_hash": hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest(),
                "original_material_digest": item.original_material_digest,
                "subject_status": item.subject_status,
                "facts": item.structured_facts, "truncated": item.truncated,
            }
            for item in evidence
        ],
        "contacts": [
            {"channel": item.channel, "value": item.value,
             "evidence_ids": item.evidence_ids, "subject_status": item.subject_status,
             "valid_format": item.valid_format}
            for item in contacts
        ],
        "policy_digest": policy.policy_digest,
    }
    digest = canonical_sha256(material)
    return LeadDossier(
        lead_id=lead_id,
        workspace_id=workspace_id,
        name=business.name,
        identity_keys=(business.source_key,) if business.source_key else (),
        research_id=research.id if research else None,
        sources=tuple(evidence),
        contacts=tuple(contacts),
        source_snapshot_digest=digest,
        truncated=any(source.truncated for source in evidence),
    )


def _validate_incremental_provenance(
    db, *, workspace_id, lead_id, research, row, metadata,
    grant_cache: dict[UUID, LeadFitResearchGrant],
    evaluation_cache: dict[UUID, LeadFitEvaluation],
) -> bool:
    """Bind each incremental row to its container, durable grant, and original bytes."""
    context = research.query_context if isinstance(research.query_context, dict) else {}
    grant_id_value = context.get("grant_id")
    plan_hash = context.get("plan_hash")
    evaluation_id_value = context.get("evaluation_id")
    if (
        research.operation != "incremental_research"
        or row.provider != "tavily"
        or row.operation not in {"search", "extract"}
        or metadata.get("provider") != row.provider
        or metadata.get("operation") != row.operation
        or not isinstance(grant_id_value, str)
        or metadata.get("grant_id") != grant_id_value
        or not isinstance(plan_hash, str)
        or len(plan_hash) != 64
        or metadata.get("plan_hash") != plan_hash
        or not isinstance(evaluation_id_value, str)
        or _research_url(row.source_url) != row.source_url
    ):
        raise DossierError("incremental evidence provenance does not match its container")
    try:
        grant_id, evaluation_id = UUID(grant_id_value), UUID(evaluation_id_value)
    except ValueError as exc:
        raise DossierError("incremental provenance identifiers are invalid") from exc

    grant = grant_cache.get(grant_id)
    if grant is None:
        grant = db.execute(select(LeadFitResearchGrant).where(
            LeadFitResearchGrant.workspace_id == workspace_id,
            LeadFitResearchGrant.lead_id == lead_id,
            LeadFitResearchGrant.id == grant_id,
        )).scalar_one_or_none()
        if grant is None:
            raise DossierError("incremental evidence references a missing or foreign grant")
        grant_cache[grant_id] = grant
    evaluation = evaluation_cache.get(evaluation_id)
    if evaluation is None:
        evaluation = db.execute(select(LeadFitEvaluation).where(
            LeadFitEvaluation.workspace_id == workspace_id,
            LeadFitEvaluation.lead_id == lead_id,
            LeadFitEvaluation.id == evaluation_id,
        )).scalar_one_or_none()
        if evaluation is None:
            raise DossierError("incremental evidence references a missing or foreign evaluation")
        evaluation_cache[evaluation_id] = evaluation

    plan = grant.plan if isinstance(grant.plan, dict) else {}
    if (
        grant.evaluation_id != evaluation.id
        or grant.policy_id != evaluation.policy_id
        or grant.source_digest != evaluation.source_digest
        or grant.plan_hash != plan_hash
        or plan.get("plan_hash") != grant.plan_hash
        or plan.get("evaluation_id") != str(evaluation.id)
        or plan.get("lead_id") != str(lead_id)
        or plan.get("policy_id") != str(grant.policy_id)
        or plan.get("source_digest") != grant.source_digest
        or research.refresh_of_id != evaluation.research_id
    ):
        raise DossierError("incremental grant, evaluation, and container bindings differ")
    material_digest = metadata.get("material_digest")
    if (
        not isinstance(material_digest, str)
        or len(material_digest) != 64
        or material_digest != hashlib.sha256(row.excerpt.encode("utf-8")).hexdigest()
    ):
        raise DossierError("incremental evidence material digest does not match original excerpt bytes")
    return True


def _business_facts(business: Business) -> dict[str, Any]:
    raw = business.raw_provider_data if isinstance(business.raw_provider_data, dict) else {}
    display = raw.get("displayName")
    if isinstance(display, dict):
        display = display.get("text")
    return _bounded_facts({
        "name": business.name,
        "category": raw.get("primaryTypeDisplayName") or raw.get("primaryType"),
        "industry": raw.get("industry"),
        "country": raw.get("country"),
        "city": raw.get("city"),
        "rating": business.rating,
        "reviews_total": business.reviews_total,
        "latitude": business.latitude,
        "longitude": business.longitude,
        "place_id": business.source_key,
        "provider_name": display,
    })


def _validate_timestamp(value: datetime | None, current_time: datetime, source: str) -> None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise DossierError(f"{source} timestamp must be timezone-aware")
    if value > current_time:
        raise DossierError(f"future {source} timestamp")


def _bounded_facts(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name", "category", "giro", "industry", "country", "city", "rating",
        "reviews_total", "latitude", "longitude", "place_id", "provider_name", "address",
        "contacts",
    }
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed or item is None:
            continue
        if key == "contacts":
            if isinstance(item, list):
                contacts = []
                for contact in item[:10]:
                    if not isinstance(contact, dict):
                        continue
                    channel, contact_value = contact.get("channel"), contact.get("value")
                    if (channel not in {"email", "phone", "whatsapp", "web_form"}
                            or not isinstance(contact_value, str)):
                        continue
                    contact_value = contact_value.strip()
                    if contact_value and len(contact_value) <= 500:
                        contacts.append({"channel": channel, "value": contact_value})
                if contacts:
                    result[key] = contacts
            continue
        if key in {"name", "category", "giro", "industry", "country", "city", "provider_name", "place_id", "address"}:
            if isinstance(item, str) and item.strip() and len(item) <= (512 if key == "place_id" else 255):
                result[key] = item.strip()
            continue
        if key == "reviews_total":
            valid_numeric = type(item) is int and 0 <= item <= 1_000_000_000
        elif key in {"rating", "latitude", "longitude"} and type(item) in (int, float):
            lower, upper = {
                "rating": (0, 5), "latitude": (-90, 90), "longitude": (-180, 180),
            }[key]
            valid_numeric = lower <= item <= upper and (
                not isinstance(item, float) or math.isfinite(item)
            )
        else:
            valid_numeric = False
        if valid_numeric:
            result[key] = item
    return result


def _valid_contact_format(channel: str, value: str) -> bool:
    if channel == "email":
        return "@" in value and "." in value.rsplit("@", 1)[-1]
    if channel in {"phone", "whatsapp"}:
        return sum(character.isdigit() for character in value) >= 7
    if channel == "web_form":
        return value.startswith(("https://", "http://"))
    return False
