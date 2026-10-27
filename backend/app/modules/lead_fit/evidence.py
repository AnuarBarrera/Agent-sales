"""Construcción de expedientes y asociación de sujetos de fuentes (T04).

Módulo puro sin DB ni proveedores. Crea LeadDossier a partir de candidatos
normalizados y vincula evidencias por identificador verificable, no por score
de búsqueda.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select

from app.modules.lead_fit.candidates import ProspectCandidate
from app.modules.lead_fit.contracts import (
    AssessmentResult,
    ContactPoint,
    EvidenceItem,
    LeadDossier,
    SourceAssessment,
    canonical_sha256,
)
from app.modules.prospect_research.models import (
    ProspectResearch,
    ProspectResearchStatus,
    ResearchEvidence,
)


def build_initial_dossier(
    *,
    workspace_id: UUID,
    lead_id: UUID | None = None,
    candidate: ProspectCandidate,
    research_id: UUID | None = None,
) -> LeadDossier:
    """
    Construye el expediente inicial de un lead a partir de un candidato
    normalizado (Maps o web). Cero proveedores: solo usa la evidencia ya
    obtenida del descubrimiento.
    """
    lead_id = lead_id or uuid4()
    sources: list[EvidenceItem] = []
    contacts: list[ContactPoint] = []
    facts = provider_structured_facts(candidate)

    evidence_id = f"ev_{candidate.provider}_{candidate.source_key[:16]}"
    sources.append(
        EvidenceItem(
            evidence_id=evidence_id,
            workspace_id=workspace_id,
            lead_id=lead_id,
            research_id=research_id,
            provider=candidate.provider,
            type="discovery",
            source_url=candidate.canonical_url or "",
            retrieved_at=datetime.now(UTC),
            excerpt=json_excerpt(candidate.raw_evidence),
            truncated=_excerpt_was_truncated(
                candidate.raw_evidence, json_excerpt(candidate.raw_evidence),
            ),
            structured_facts=facts,
            content_hash=_content_hash(candidate.raw_evidence),
            subject_status="unresolved",
            association_method="discovery_snapshot",
            association_confidence=None,
        )
    )

    for contact in facts.get("contacts", []):
        contacts.append(
            ContactPoint(
                channel=contact["channel"],
                value=contact["value"],
                evidence_ids=(evidence_id,),
                subject_status="unresolved",
                valid_format=_valid_channel_format(contact["channel"], contact["value"]),
            )
        )

    return LeadDossier(
        lead_id=lead_id,
        workspace_id=workspace_id,
        name=candidate.name,
        identity_keys=(candidate.source_key,),
        research_id=research_id,
        sources=tuple(sources),
        contacts=tuple(contacts),
        source_snapshot_digest=None,
        truncated=any(source.truncated for source in sources),
    )


def persist_discovery_snapshot(
    db,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    candidate: ProspectCandidate,
    query: str,
    max_age_hours: int = 720,
    policy_digest: str | None = None,
    now: datetime | None = None,
) -> ProspectResearch:
    """Persist original Maps/web discovery material, reusing unchanged fresh snapshots."""
    retrieved_at = now or datetime.now(UTC)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("discovery timestamp must be timezone-aware")
    if not 1 <= max_age_hours <= 24 * 365:
        raise ValueError("invalid discovery snapshot lifetime")
    provider = candidate.provider
    if provider not in {"google_places", "tavily"}:
        raise ValueError("unsupported discovery snapshot provider")
    operation = "places_snapshot" if provider == "google_places" else "discovery_snapshot"
    facts = provider_structured_facts(candidate)
    excerpt = json_excerpt(candidate.raw_evidence)[:4000]
    truncated = _excerpt_was_truncated(candidate.raw_evidence, excerpt)
    source_url = candidate.canonical_url or ""
    if provider == "google_places" and candidate.real_place_id:
        source_url = f"https://www.google.com/maps/search/?api=1&query_place_id={candidate.real_place_id}"
    if not source_url.startswith(("https://", "http://")):
        raise ValueError("discovery source URL is invalid")
    material_digest = canonical_sha256({
        "provider": provider, "operation": operation, "source_key": candidate.source_key,
        "query": query.strip(), "url": source_url, "excerpt": excerpt, "facts": facts,
        "policy_digest": policy_digest,
    })

    latest = db.execute(select(ProspectResearch).where(
        ProspectResearch.workspace_id == workspace_id,
        ProspectResearch.lead_id == lead_id,
        ProspectResearch.provider == provider,
        ProspectResearch.operation == operation,
        ProspectResearch.status == ProspectResearchStatus.COMPLETED,
    ).order_by(ProspectResearch.created_at.desc()).limit(1)).scalar_one_or_none()
    if latest is not None:
        prior_digest = (latest.query_context or {}).get("snapshot_digest")
        created_at = latest.completed_at or latest.created_at
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("stored snapshot timestamp must be timezone-aware")
        if created_at > retrieved_at:
            raise ValueError("stored snapshot timestamp is in the future")
        if (prior_digest == material_digest
                and timedelta(0) <= retrieved_at - created_at <= timedelta(hours=max_age_hours)):
            return latest

    snapshot = ProspectResearch(
        workspace_id=workspace_id,
        lead_id=lead_id,
        status=ProspectResearchStatus.COMPLETED,
        provider=provider,
        operation=operation,
        query_summary=query.strip()[:500],
        query_context={
            "snapshot_digest": material_digest,
            "source_key": candidate.source_key,
            "policy_digest": policy_digest,
        },
        requested_at=retrieved_at,
        started_at=retrieved_at,
        completed_at=retrieved_at,
        result_count=1,
    )
    db.add(snapshot)
    db.flush()
    evidence = ResearchEvidence(
        evidence_id=f"ev_{provider}_{canonical_sha256({
            'workspace_id': str(workspace_id),
            'lead_id': str(lead_id),
            'research_id': str(snapshot.id),
            'key': candidate.source_key,
            'material': material_digest,
        })[:32]}",
        workspace_id=workspace_id,
        research_id=snapshot.id,
        lead_id=lead_id,
        provider=provider,
        operation=operation,
        source_type="maps_place" if provider == "google_places" else "web_result",
        source_url=source_url,
        source_title=candidate.name[:500],
        excerpt=excerpt,
        retrieved_at=retrieved_at,
        relevance_score=None,
        metadata_json={
            "source_key": candidate.source_key,
            "snapshot_digest": material_digest,
            "structured_facts": facts,
            "truncated": truncated,
        },
    )
    db.add(evidence)
    db.flush()
    return snapshot


_THIRD_PARTY_PROFILES = frozenset({
    "doctoralia.com.mx", "doctoralia.es", "yelp.com", "facebook.com", "fb.com",
    "instagram.com", "linkedin.com", "google.com", "maps.google.com",
    "tripadvisor.com", "linktr.ee",
})


def _observed_website(raw: dict[str, Any]) -> str | None:
    # Keep a provider observation, never infer ownership, uptime or absence.
    from app.modules.prospect_research.service import _research_url

    nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    for value in (raw.get("website"), nested.get("websiteUri")):
        url = _research_url(value)
        if url is not None:
            return url
    return None


def _observed_website_kind(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return "third_party_profile" if any(
        host == domain or host.endswith("." + domain) for domain in _THIRD_PARTY_PROFILES
    ) else "unverified_site"


def provider_structured_facts(candidate: ProspectCandidate) -> dict[str, Any]:
    """Select exact-rule facts from original, explicitly recognized provider fields."""
    raw = candidate.raw_evidence if isinstance(candidate.raw_evidence, dict) else {}
    nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    location = nested.get("location") if isinstance(nested.get("location"), dict) else {}
    facts = {"name": candidate.name}
    declared_contacts = []
    for contact in candidate.contacts[:10]:
        if not isinstance(contact, dict):
            continue
        channel, value = contact.get("channel"), contact.get("value")
        if (channel in {"email", "phone", "whatsapp", "web_form"}
                and isinstance(value, str) and value.strip()):
            declared_contacts.append({"channel": channel, "value": value.strip()[:500]})
    if declared_contacts:
        facts["contacts"] = declared_contacts
    if candidate.provider == "google_places":
        website = _observed_website(raw)
        if website is not None:
            facts["website_url"] = website
            facts["website_kind"] = _observed_website_kind(website)
        candidate_fields = {
            "rating": raw.get("rating"),
            "reviews_total": raw.get("reviews_total"),
            "latitude": raw.get("latitude", location.get("latitude")),
            "longitude": raw.get("longitude", location.get("longitude")),
        }
        facts.update({key: value for key, value in candidate_fields.items() if value is not None})
        facts["place_id"] = candidate.real_place_id or candidate.source_key
        address = raw.get("address") or nested.get("formattedAddress")
        if isinstance(address, str) and address.strip():
            facts["address"] = address.strip()[:255]
        category = (
            raw.get("category") or nested.get("primaryTypeDisplayName")
            or nested.get("primaryType")
        )
        if isinstance(category, dict):
            category = category.get("text")
        if isinstance(category, str) and category.strip():
            facts["category"] = category.strip()[:255]
        for field in ("category", "giro", "industry", "country", "city"):
            value = raw.get(field) or nested.get(field)
            if field != "category" and isinstance(value, str) and value.strip():
                facts[field] = value.strip()[:255]
        # websiteUri is original Places provider material. A generic Business.website
        # value never becomes a contact channel; only an explicit wa.me URI qualifies.
        website_uri = nested.get("websiteUri")
        if isinstance(website_uri, str) and _is_explicit_wa_me(website_uri):
            facts.setdefault("contacts", []).append({"channel": "whatsapp", "value": website_uri})
    else:
        # Web identity and semantic content do not imply exact commercial facts.
        facts["source_key"] = candidate.source_key
    return facts


def _is_explicit_wa_me(value: str) -> bool:
    if len(value) > 300 or any(ord(character) <= 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == "wa.me"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and parsed.query == ""
        and parsed.fragment == ""
        and re.fullmatch(r"/[0-9]{7,20}/?", parsed.path)
    )


def _excerpt_was_truncated(raw: dict[str, Any], excerpt: str) -> bool:
    """Report truncation from the explicit 1,000-character source excerpt bound."""
    source_values = [
        raw.get(key) for key in (
            "name", "title", "content", "displayName", "formattedAddress",
            "address", "category",
        )
    ]
    if any(isinstance(value, str) and len(value) > 200 for value in source_values):
        return True
    display = raw.get("displayName")
    if isinstance(display, dict) and isinstance(display.get("text"), str) and len(display["text"]) > 200:
        return True
    native = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    for key in ("primaryType", "primaryTypeDisplayName", "formattedAddress"):
        value = native.get(key)
        if isinstance(value, dict):
            value = value.get("text")
        if isinstance(value, str) and len(value) > 200:
            return True
    return len(excerpt) >= 1000


def load_dossier(
    *,
    workspace_id: UUID,
    lead_id: UUID,
    sources: tuple[EvidenceItem, ...],
    contacts: tuple[ContactPoint, ...],
    name: str | None = None,
    research_id: UUID | None = None,
    source_snapshot_digest: str | None = None,
) -> LeadDossier:
    """Carga un expediente desde evidencias y contactos ya persistidos."""
    return LeadDossier(
        lead_id=lead_id,
        workspace_id=workspace_id,
        name=name,
        identity_keys=(),
        research_id=research_id,
        sources=sources,
        contacts=contacts,
        source_snapshot_digest=source_snapshot_digest,
        truncated=False,
    )


def bind_evidence_subjects(
    dossier: LeadDossier,
    answers: dict[str, SourceAssessment] | None = None,
    *,
    entity_confidence_floor: float = 0.90,
) -> LeadDossier:
    """
    Vincula evidencias a sujetos usando respuestas de asociación (Jev) o
    identificador exacto verificable.

    answers: mapping question_id -> SourceAssessment (status same_business /
    different_business / insufficient). Solo same_business con confianza
    suficiente marca matched; insuficiente deja unresolved.
    """
    answers = answers or {}
    updated_sources = list(dossier.sources)
    for idx, source in enumerate(updated_sources):
        # Buscar la respuesta de entidad correspondiente a esta evidencia.
        entity_qid = f"entity_{source.evidence_id}"
        answer = answers.get(entity_qid)
        if answer is None or answer.evidence_id != source.evidence_id:
            continue
        status = answer.status
        # Para preguntas de entidad, matches=same_business, does_not_match=
        # different_business, not_established=insufficient.
        confidence_is_sufficient = (
            answer.confidence is not None
            and answer.confidence >= entity_confidence_floor
        )
        subject_status = "unresolved"
        if status is AssessmentResult.MATCHES and confidence_is_sufficient:
            subject_status = "matched"
        elif (
            status is AssessmentResult.DOES_NOT_MATCH
            and confidence_is_sufficient
        ):
            subject_status = "mismatched"
        updated_sources[idx] = source.model_copy(
            update={
                "subject_status": subject_status,
                "association_method": "jev_entity",
                "association_confidence": answer.confidence,
            }
        )

    # Actualizar contactos cuyos evidence_ids apunten a evidencias ahora matched/mismatched.
    subject_by_evidence = {
        s.evidence_id: s.subject_status for s in updated_sources
    }
    updated_contacts = []
    for contact in dossier.contacts:
        linked_statuses = [subject_by_evidence.get(eid) for eid in contact.evidence_ids]
        contact_status = "unresolved"
        if linked_statuses and all(st == "matched" for st in linked_statuses):
            contact_status = "matched"
        elif linked_statuses and all(st == "mismatched" for st in linked_statuses):
            contact_status = "mismatched"
        updated_contacts.append(
            contact.model_copy(
                update={
                    "subject_status": contact_status,
                }
            )
        )

    return dossier.model_copy(
        update={
            "sources": tuple(updated_sources),
            "contacts": tuple(updated_contacts),
        }
    )


def json_excerpt(evidence: dict[str, Any]) -> str:
    """Extrae un excerto corto y legible de la evidencia cruda."""
    parts = []
    nested = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
    selected = (
        ("name", evidence.get("name")),
        ("website_url_observed", _observed_website(evidence)),
        ("website_kind_observed", _observed_website_kind(_observed_website(evidence))
         if _observed_website(evidence) else None),
        ("title", evidence.get("title")),
        ("content", evidence.get("content")),
        ("address", evidence.get("address") or nested.get("formattedAddress")),
        ("category", evidence.get("category") or nested.get("primaryTypeDisplayName")
         or nested.get("primaryType")),
        ("rating", evidence.get("rating")),
        ("reviews_total", evidence.get("reviews_total")),
        ("displayName", evidence.get("displayName")),
        ("formattedAddress", evidence.get("formattedAddress")),
    )
    for key, value in selected:
        if isinstance(value, dict):
            value = value.get("text")
        if isinstance(value, str) and value:
            parts.append(f"{key}: {value[:200]}")
        elif isinstance(value, int | float) and not isinstance(value, bool):
            parts.append(f"{key}: {value}")
    return " | ".join(parts)[:1000] or str(evidence)[:1000]


def _content_hash(evidence: dict[str, Any]) -> str:
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _valid_channel_format(channel: str, value: str) -> bool:
    """Validación de formato básica (no verifica deliverability real)."""
    if not value:
        return False
    if channel == "email":
        return "@" in value and "." in value.split("@")[-1] if "@" in value else False
    if channel == "phone":
        digits = "".join(c for c in value if c.isdigit() or c == "+")
        return len(digits) >= 7
    if channel == "web_form":
        return value.startswith(("http://", "https://"))
    if channel == "whatsapp":
        digits = "".join(c for c in value if c.isdigit() or c == "+")
        return len(digits) >= 7
    return True
