"""Conservative deterministic divergence detection for critical source facts."""

from __future__ import annotations

import re
from typing import Any

from app.modules.sales_playbook.extraction import map_markdown_sections
from app.modules.sales_playbook.semantic_playbook import SourceClaim, SourceConflictReview

_AMOUNT = r"(?:\$\s*)?([0-9][0-9,.]*)(?:\s*(k|mil))?"
_MONEY = re.compile(
    rf"(?<![\w]){_AMOUNT}(?:\s*(?:-|–|a|/)\s*{_AMOUNT})?(?:\s*(?:MXN|pesos?))?", re.I,
)
_STOP = {"de", "del", "la", "el", "en", "con", "para", "por", "y", "a", "the", "and", "in", "of"}
_PLACES = {
    "mexico_city": ("cdmx", "ciudad de méxico", "ciudad de mexico"),
    "guadalajara": ("guadalajara",),
    "monterrey": ("monterrey",),
    "puebla": ("puebla",),
    "queretaro": ("querétaro", "queretaro"),
    "tijuana": ("tijuana",),
}


def _prices(text: str) -> set[int]:
    result = set()
    for match in _MONEY.finditer(text):
        raw, scale, second, second_scale = match.groups()
        context = text[max(0, match.start() - 12):match.end() + 12].lower()
        if "$" not in context and not re.search(r"\b(?:mxn|pesos?|inversi[oó]n|precio|costo)\b", context):
            continue
        for amount, amount_scale in ((raw, scale), (second, second_scale)):
            if amount is None:
                continue
            try:
                number = int(float(amount.replace(",", "")))
            except ValueError:
                continue
            multiplier = 1000 if amount_scale or (second_scale and amount == raw) else 1
            result.add(number * multiplier)
    return result


def _money_sections(text: str) -> list[tuple[str, str]]:
    result = []
    for section in map_markdown_sections(text):
        if _prices(section.text):
            result.append((section.id, section.text[:1000]))
    return result


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-záéíóúüñ]{3,}", text.lower()) if word not in _STOP}


def _profile_claim(field: str, value: str) -> SourceClaim:
    return SourceClaim(origin="seller_profile", label=f"SellerProfile · {field}", value=value[:1000])


def detect_source_conflicts(
    profile: Any, source_text: str, *, document_origin: str = "pasted_document",
) -> list[SourceConflictReview]:
    if profile is None or not source_text.strip():
        return []
    conflicts = []
    price_context = (getattr(profile, "price_context", None) or "").strip()
    source_prices = _money_sections(source_text)
    profile_prices = _prices(price_context)
    document_prices = set().union(*(_prices(text) for _, text in source_prices)) if source_prices else set()
    source_price_values = {tuple(sorted(_prices(value))) for _, value in source_prices}
    has_internal_price_divergence = len(source_price_values) > 1
    profile_diverges = bool(price_context and profile_prices and document_prices and profile_prices.isdisjoint(document_prices))
    if source_price_values and (profile_diverges or has_internal_price_divergence):
        claims = [_profile_claim("price_context", price_context)] if profile_diverges else []
        claims.extend(SourceClaim(
            origin=document_origin, label="Documento de ventas", value=value,
            source_section_ids=[section_id],
        ) for section_id, value in source_prices)
        conflicts.append(SourceConflictReview(
            conflict_id="source_conflict:price_context", field="price_context", claims=claims[:10],
        ))

    target = (getattr(profile, "target_customer_summary", None) or "").strip()
    target_sections = [section for section in map_markdown_sections(source_text)
                      if re.search(r"cliente|p[uú]blico|ideal|target", section.heading, re.I)]
    if target and target_sections:
        profile_tokens = _tokens(target)
        source_values = [(section.id, section.text[:1000]) for section in target_sections if section.text.strip()]
        source_tokens = set().union(*(_tokens(value) for _, value in source_values)) if source_values else set()
        overlap = len(profile_tokens & source_tokens) / max(1, len(profile_tokens))
        # Low lexical overlap is only a divergence signal for a human, never
        # a semantic claim that the two audiences are mutually exclusive.
        if profile_tokens and source_tokens and overlap < 0.2:
            conflicts.append(SourceConflictReview(
                conflict_id="source_conflict:target_customer", field="target_customer",
                claims=[_profile_claim("target_customer_summary", target), *[
                    SourceClaim(origin=document_origin, label="Documento de ventas", value=value,
                                source_section_ids=[section_id])
                    for section_id, value in source_values[:8]
                ]],
            ))

    offer = (getattr(profile, "offer_summary", None) or "").strip()
    offer_sections = [section for section in map_markdown_sections(source_text)
                      if re.search(r"oferta|producto|servicio", section.heading, re.I)]
    offer_values = [(section.id, section.text[:1000]) for section in offer_sections if section.text.strip()]
    if offer and offer_values:
        profile_tokens = _tokens(offer)
        source_tokens = set().union(*(_tokens(value) for _, value in offer_values))
        overlap = len(profile_tokens & source_tokens) / max(1, len(profile_tokens))
        if profile_tokens and source_tokens and overlap < 0.2:
            conflicts.append(SourceConflictReview(
                conflict_id="source_conflict:offer_summary", field="offer_summary",
                claims=[_profile_claim("offer_summary", offer), *[
                    SourceClaim(origin=document_origin, label="Documento de ventas", value=value,
                                source_section_ids=[section_id])
                    for section_id, value in offer_values[:8]
                ]],
            ))

    market = (getattr(profile, "location_or_market", None) or "").strip()
    market_sections = map_markdown_sections(source_text)
    profile_places = {canonical for canonical, aliases in _PLACES.items()
                      if any(alias in market.lower() for alias in aliases)}
    source_values = [(section.id, section.text[:1000]) for section in market_sections if section.text.strip()]
    source_places = {canonical for _, value in source_values for canonical, aliases in _PLACES.items()
                     if any(alias in value.lower() for alias in aliases)}
    if profile and profile_places and source_places and profile_places.isdisjoint(source_places):
        conflicts.append(SourceConflictReview(
            conflict_id="source_conflict:location_or_market", field="location_or_market",
            claims=[_profile_claim("location_or_market", market), *[
                SourceClaim(origin=document_origin, label="Documento de ventas", value=value,
                            source_section_ids=[section_id])
                for section_id, value in source_values[:8]
            ]],
        ))
    return conflicts
