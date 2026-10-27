"""Normalización de candidatos de descubrimiento Maps/web (T04).

Módulo puro sin dependencia de DB, settings ni proveedores.
Produzca ProspectCandidate con identidad genérica (provider/source_key)
sin asumir que place_id es un Google Place ID.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode


@dataclass(frozen=True)
class PlaceCandidate:
    """Candidato proveniente de Google Places New."""

    place_id: str
    name: str
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    reviews_total: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    raw_provider_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class TavilySearchResult:
    """Candidato proveniente de búsqueda web Tavily."""

    url: str
    title: str
    content: str
    score: float | None = None
    raw_provider_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProspectCandidate:
    """
    Candidato normalizado con identidad genérica.

    provider: 'google_places' | 'tavily' | 'csv_import' | 'desconocido'
    source_key: identificador estable del proveedor (place_id real para Google,
                hash de identidad canónica para web, etc.)
    real_place_id: solo rellenado si provider=='google_places' y se confirmó.
    """

    provider: str
    source_key: str
    real_place_id: str | None
    name: str
    canonical_url: str | None
    raw_evidence: dict[str, Any]
    contacts: list[dict[str, Any]]


def canonical_identity_hash(*parts: str) -> str:
    """Hash estable de identidad canónica para candidatos web sin ID fiable."""
    identity = "|".join(part or "" for part in parts)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def normalize_place_candidate(place: PlaceCandidate) -> ProspectCandidate:
    """
    Normaliza un candidato de Google Places New.

    El source_key es el place_id real de Google. real_place_id se rellena
    solo cuando la procedencia es Google demostrada.
    """
    contacts: list[dict[str, Any]] = []
    if place.phone:
        contacts.append(
            {"channel": "phone", "value": place.phone, "source": "google_places"}
        )

    canonical_url = place.website or (
        "https://www.google.com/maps/search/?"
        + urlencode(
            {
                "api": "1",
                "query": place.name,
                "query_place_id": place.place_id,
            }
        )
    )

    return ProspectCandidate(
        provider="google_places",
        source_key=place.place_id,
        real_place_id=place.place_id,
        name=place.name,
        canonical_url=canonical_url,
        raw_evidence={
            "provider": "google_places",
            "place_id": place.place_id,
            "name": place.name,
            "address": place.address,
            "phone": place.phone,
            "website": place.website,
            "rating": place.rating,
            "reviews_total": place.reviews_total,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "raw": place.raw_provider_data,
        },
        contacts=contacts,
    )


def normalize_web_candidate(result: TavilySearchResult) -> ProspectCandidate:
    """
    Normaliza un resultado de búsqueda web Tavily.

    Para web no se exige coordenadas ni Place ID. La identidad es un hash
    estable de la URL canónica y el título; no se inventa Google Place ID.
    Si la identidad es insuficiente, el candidato queda con source_key de hash
    y real_place_id=None (unresolved).
    """
    identity_hash = canonical_identity_hash(result.url, result.title)
    contacts: list[dict[str, Any]] = []

    return ProspectCandidate(
        provider="tavily",
        source_key=identity_hash,
        real_place_id=None,
        name=result.title,
        canonical_url=result.url,
        raw_evidence={
            "provider": "tavily",
            "url": result.url,
            "title": result.title,
            "content": result.content,
            "score": result.score,
            "raw": result.raw_provider_data,
        },
        contacts=contacts,
    )


def normalize_csv_candidate(
    *,
    name: str,
    place_id: str | None = None,
    phone: str | None = None,
    website: str | None = None,
    address: str | None = None,
    raw: dict[str, Any] | None = None,
) -> ProspectCandidate:
    """
    Normaliza un candidato importado por CSV (legacy n8n).

    El place_id de importación es un fallback generado (prefijo 'csv:'), no un
    Google Place ID. Se marca como provider='csv_import' y source_key es el
    place_id del CSV, distinguiéndolo claramente de los IDs de Google.
    """
    contacts: list[dict[str, Any]] = []
    if phone:
        contacts.append({"channel": "phone", "value": phone, "source": "csv_import"})
    return ProspectCandidate(
        provider="csv_import",
        source_key=place_id or canonical_identity_hash(name, phone or "", address or ""),
        real_place_id=None,  # un fallback de CSV nunca es un Google Place ID
        name=name,
        canonical_url=website,
        raw_evidence={
            "provider": "csv_import",
            "place_id": place_id,
            "name": name,
            "phone": phone,
            "website": website,
            "address": address,
            "raw": raw or {},
        },
        contacts=contacts,
    )


def is_google_place_id(source_key: str) -> bool:
    """Devuelve True solo si el source_key parece un Google Place ID real.

    Los IDs de Google no tienen prefijo 'csv:' ni son hashes de 24 chars de
    identidad canónica. Esta heurística evita confundir imports con Google IDs.
    """
    return bool(
        source_key
        and not source_key.startswith("csv:")
        and not (
            len(source_key) == 24
            and all(c in "0123456789abcdef" for c in source_key)
        )
    )


def candidate_key(workspace_id: str, provider: str, source_key: str) -> str:
    """Clave única de unicidad cross-provider: workspace+provider+source_key."""
    payload = json.dumps(
        {"workspace_id": workspace_id, "provider": provider, "source_key": source_key},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
