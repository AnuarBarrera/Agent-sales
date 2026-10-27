"""Parse and import lead exports produced by the legacy n8n Google Sheets flow."""

import csv
import hashlib
import io
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.leads.models import Business, Lead, LeadProfile, ValidationStatus


class LeadImportError(ValueError):
    """Raised when a CSV cannot be converted into a valid lead row."""


@dataclass(frozen=True)
class ImportedLeadRow:
    place_id: str
    name: str
    phone: str | None = None
    address: str | None = None
    website: str | None = None
    rating: float | None = None
    reviews_total: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    giro: str | None = None
    zona: str | None = None
    web_status: str | None = None
    web_check_detail: str | None = None
    google_presence: str | None = None
    meta_ads_active: bool | None = None
    profile: str | None = None
    validation_status: str = "pending"
    score: int | None = None
    score_reason: str | None = None
    contacted: bool | None = None
    source_searched_at: datetime | None = None
    outreach_message: str | None = None
    outreach_message_generated_at: datetime | None = None
    outreach_message_error: str | None = None
    raw: dict[str, str] | None = None


def apply_imported_row(business: object, lead: object, row: ImportedLeadRow) -> None:
    """Apply non-empty CSV values while keeping existing CRM values intact."""
    business_fields = {
        "name": row.name,
        "phone": row.phone,
        "address": row.address,
        "website": row.website,
        "rating": row.rating,
        "reviews_total": row.reviews_total,
        "latitude": row.latitude,
        "longitude": row.longitude,
    }
    for field, value in business_fields.items():
        if value is not None:
            setattr(business, field, value)

    if getattr(business, "provider", None) != "google_places":
        business.provider = "csv_import"
        business.source_key = row.place_id
    if getattr(business, "place_id", None) is None:
        business.place_id = row.place_id

    lead_fields = {
        "profile": row.profile,
        "validation_status": row.validation_status if row.validation_status != "pending" else None,
        "score": row.score,
        "score_reason": row.score_reason,
        "giro": row.giro,
        "zona": row.zona,
        "web_status": row.web_status,
        "web_check_detail": row.web_check_detail,
        "google_presence": row.google_presence,
        "meta_ads_active": row.meta_ads_active,
        "contacted": row.contacted,
        "source_searched_at": row.source_searched_at,
        "outreach_message": row.outreach_message,
        "outreach_message_generated_at": row.outreach_message_generated_at,
        "outreach_message_error": row.outreach_message_error,
    }
    for field, value in lead_fields.items():
        if value is not None:
            setattr(lead, field, value)

    existing_raw = getattr(lead, "raw_import_data", None) or {}
    existing_raw.update(row.raw or {})
    lead.raw_import_data = existing_raw


def normalize_header(value: str) -> str:
    """Normalize Spanish/English headers without changing their meaning."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "".join(character for character in value.lower() if character.isalnum())


_HEADER_ALIASES: dict[str, set[str]] = {
    "place_id": {"placeid", "googleplaceid", "idgoogle"},
    "name": {"nombre", "nombredelnegocio", "name", "negocio"},
    "phone": {"telefono", "phone", "tel", "celular", "movil"},
    "address": {"direccion", "address", "domicilio"},
    "website": {"web", "website", "sitioweb", "paginaweb"},
    "rating": {"rating", "calificacion"},
    "reviews_total": {"resenias", "reviews", "reviewstotal", "totalresenias"},
    "latitude": {"lat", "latitude", "latitud"},
    "longitude": {"lng", "lon", "longitude", "longitud"},
    "giro": {"giro", "categoria", "category"},
    "zona": {"zona", "ubicacion", "location"},
    "web_status": {"webstatus", "estadoweb"},
    "web_check_detail": {"webdetalle", "webcheckdetail", "detalleweb"},
    "google_presence": {"googlepresence", "presenciagoogle"},
    "meta_ads_active": {"metaads", "metaadsactivos", "metaadsactivo"},
    "profile": {"perfil", "profile", "clasificacion"},
    "score": {"score", "puntuacion", "puntaje"},
    "score_reason": {"razon", "razonscore", "scorereason"},
    "contacted": {"contactado", "contacted"},
    "source_searched_at": {"fecha", "fechabusqueda", "searchedat", "fechadebusqueda"},
    "outreach_message": {"mensajefrio", "outreachmessage"},
    "outreach_message_generated_at": {"mensajegenerado", "outreachmessagegeneratedat"},
    "outreach_message_error": {"mensajefrioerror", "outreachmessageerror"},
}


def _field_map(headers: list[str]) -> dict[str, str]:
    normalized = {normalize_header(header): header for header in headers if header}
    fields: dict[str, str] = {}
    for field, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                fields[field] = normalized[alias]
                break
    return fields


def _text(row: dict[str, str], field_map: dict[str, str], field: str) -> str | None:
    header = field_map.get(field)
    value = row.get(header, "") if header else ""
    value = value.strip() if value is not None else ""
    return value or None


def _number(value: str | None, field: str, row_number: int, integer: bool = False) -> float | int | None:
    if not value:
        return None
    try:
        parsed = float(value.replace(",", "."))
        return int(parsed) if integer else parsed
    except ValueError as exc:
        raise LeadImportError(f"fila {row_number}: {field} no es numérico") from exc


def _boolean(value: str | None) -> bool | None:
    if not value:
        return None
    normalized = normalize_header(value)
    if normalized in {"true", "1", "si", "yes", "activo", "activos", "x"}:
        return True
    if normalized in {"false", "0", "no", "none", "null", "inactivo", "inactivos"}:
        return False
    return None


def _datetime(value: str | None, field: str, row_number: int) -> datetime | None:
    if not value:
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise LeadImportError(f"fila {row_number}: {field} no tiene una fecha válida") from exc


def _fallback_place_id(name: str, phone: str | None, address: str | None) -> str:
    identity = "|".join((name.casefold(), phone or "", address or ""))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"csv:{digest}"


def _canonical_profile(value: str | None) -> LeadProfile | None:
    if not value:
        return None
    return {
        "oro": LeadProfile.ORO,
        "agencia": LeadProfile.AGENCIA,
        "inquilino": LeadProfile.INQUILINO,
        "frio": LeadProfile.FRIO,
    }.get(normalize_header(value))


def _canonical_web_status(value: str | None) -> str | None:
    if not value:
        return None
    return {"valida": "valida", "invalida": "invalida"}.get(normalize_header(value), value)


def _read_text(file: BinaryIO) -> str:
    content = file.read()
    if isinstance(content, str):
        return content
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise LeadImportError("El archivo CSV no tiene una codificación compatible")


def parse_leads_csv(file: BinaryIO) -> list[ImportedLeadRow]:
    """Parse a Google Sheets/n8n CSV export, accepting search-only or validated rows."""
    text = _read_text(file)
    if not text.strip():
        raise LeadImportError("El archivo CSV está vacío")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise LeadImportError("El CSV no contiene cabeceras")
    field_map = _field_map(reader.fieldnames)
    if "name" not in field_map:
        raise LeadImportError("El CSV debe contener la cabecera Nombre")

    rows: list[ImportedLeadRow] = []
    for row_number, source_row in enumerate(reader, start=2):
        row = {key: (value or "") for key, value in source_row.items() if key is not None}
        name = _text(row, field_map, "name")
        if not name:
            raise LeadImportError(f"fila {row_number}: falta la columna Nombre")

        phone = _text(row, field_map, "phone")
        address = _text(row, field_map, "address")
        place_id = _text(row, field_map, "place_id") or _fallback_place_id(name, phone, address)
        has_validation = any(
            _text(row, field_map, field)
            for field in ("web_status", "google_presence", "meta_ads_active", "profile", "score")
        )
        profile = _canonical_profile(_text(row, field_map, "profile"))

        rows.append(
            ImportedLeadRow(
                place_id=place_id,
                name=name,
                phone=phone,
                address=address,
                website=_text(row, field_map, "website"),
                rating=_number(_text(row, field_map, "rating"), "Rating", row_number),  # type: ignore[arg-type]
                reviews_total=_number(_text(row, field_map, "reviews_total"), "Resenias", row_number, integer=True),  # type: ignore[arg-type]
                latitude=_number(_text(row, field_map, "latitude"), "Latitud", row_number),  # type: ignore[arg-type]
                longitude=_number(_text(row, field_map, "longitude"), "Longitud", row_number),  # type: ignore[arg-type]
                giro=_text(row, field_map, "giro"),
                zona=_text(row, field_map, "zona"),
                web_status=_canonical_web_status(_text(row, field_map, "web_status")),
                web_check_detail=_text(row, field_map, "web_check_detail"),
                google_presence=_text(row, field_map, "google_presence"),
                meta_ads_active=_boolean(_text(row, field_map, "meta_ads_active")),
                profile=profile,
                validation_status="complete" if profile and has_validation else "partial" if has_validation else "pending",
                score=_number(_text(row, field_map, "score"), "Score", row_number, integer=True),  # type: ignore[arg-type]
                score_reason=_text(row, field_map, "score_reason"),
                contacted=_boolean(_text(row, field_map, "contacted")),
                source_searched_at=_datetime(_text(row, field_map, "source_searched_at"), "Fecha", row_number),
                outreach_message=_text(row, field_map, "outreach_message"),
                outreach_message_generated_at=_datetime(
                    _text(row, field_map, "outreach_message_generated_at"),
                    "Mensaje generado",
                    row_number,
                ),
                outreach_message_error=_text(row, field_map, "outreach_message_error"),
                raw=row,
            )
        )
    return rows


@dataclass(frozen=True)
class LeadImportResult:
    created: int
    updated: int
    skipped: int
    errors: list[str]


def _find_existing_business(db: Session, workspace_id: object, row: ImportedLeadRow) -> Business | None:
    business = db.execute(
        select(Business).where(
            Business.workspace_id == workspace_id,
            Business.place_id == row.place_id,
        )
    ).scalar_one_or_none()
    if business:
        return business

    for field, value in ((Business.phone, row.phone), (Business.website, row.website)):
        if value:
            business = db.execute(
                select(Business).where(Business.workspace_id == workspace_id, field == value)
            ).scalars().first()
            if business:
                return business
    return None


def import_lead_rows(db: Session, workspace_id: object, rows: list[ImportedLeadRow]) -> LeadImportResult:
    """Upsert parsed rows into the current workspace."""
    created = 0
    updated = 0
    skipped = 0
    seen: set[str] = set()

    for row in rows:
        if row.place_id in seen:
            skipped += 1
            continue
        seen.add(row.place_id)

        business = _find_existing_business(db, workspace_id, row)
        if business is None:
            business = Business(
                workspace_id=workspace_id,
                place_id=row.place_id,
                provider="csv_import",
                source_key=row.place_id,
                name=row.name,
            )
            db.add(business)
            db.flush()

        lead = db.execute(
            select(Lead).where(
                Lead.workspace_id == workspace_id,
                Lead.business_id == business.id,
            )
        ).scalar_one_or_none()
        if lead is None:
            lead = Lead(
                workspace_id=workspace_id,
                business_id=business.id,
                profile=LeadProfile.UNCLASSIFIED,
                validation_status=ValidationStatus.PENDING,
            )
            db.add(lead)
            created += 1
        else:
            updated += 1

        apply_imported_row(business, lead, row)
        if row.profile:
            lead.profile = row.profile
        if row.validation_status != "pending":
            lead.validation_status = row.validation_status
        db.flush()

    return LeadImportResult(created=created, updated=updated, skipped=skipped, errors=[])
