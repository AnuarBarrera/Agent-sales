"""Safe evaluator for the small, explicitly supported Lead Fit rule language."""

from __future__ import annotations

import math
from typing import Any

from app.modules.lead_fit.contracts import (
    AssessmentResult,
    CriterionSpec,
    ExactRuleFieldIn,
    ExactRuleGeoWithin,
    ExactRuleNumericRange,
)

ALLOWED_FIELDS = frozenset(
    {
        "name", "category", "giro", "industry", "country", "city",
        "rating", "reviews_total", "latitude", "longitude",
    }
)
_TEXT_FIELDS = frozenset({"name", "category", "giro", "industry", "country", "city"})


def evaluate_exact_rule(criterion: CriterionSpec, facts: dict[str, Any]) -> AssessmentResult:
    """Evaluate a validated exact rule; malformed or missing facts stay unknown."""
    rule = criterion.exact_rule
    if rule is None or not isinstance(facts, dict):
        return AssessmentResult.NOT_ESTABLISHED

    if isinstance(rule, ExactRuleFieldIn):
        field = rule.field
        if field not in ALLOWED_FIELDS or field not in _TEXT_FIELDS:
            return AssessmentResult.NOT_ESTABLISHED
        value = facts.get(field)
        if not isinstance(value, str) or not value.strip():
            return AssessmentResult.NOT_ESTABLISHED
        accepted = {item.casefold() for item in rule.values}
        return _result(value.strip().casefold() in accepted)

    if isinstance(rule, ExactRuleNumericRange):
        field = rule.field
        if field not in ALLOWED_FIELDS or field not in {"rating", "reviews_total"}:
            return AssessmentResult.NOT_ESTABLISHED
        value = facts.get(field)
        if type(value) not in (int, float):
            return AssessmentResult.NOT_ESTABLISHED
        if value < 0 or value > (1_000_000_000 if field == "reviews_total" else 5):
            return AssessmentResult.NOT_ESTABLISHED
        if isinstance(value, float) and not math.isfinite(value):
            return AssessmentResult.NOT_ESTABLISHED
        if field == "reviews_total" and (type(value) is not int or value < 0):
            return AssessmentResult.NOT_ESTABLISHED
        if field == "rating" and not 0 <= value <= 5:
            return AssessmentResult.NOT_ESTABLISHED
        return _result(
            (rule.min is None or value >= rule.min)
            and (rule.max is None or value <= rule.max)
        )

    if isinstance(rule, ExactRuleGeoWithin):
        latitude, longitude = facts.get("latitude"), facts.get("longitude")
        if not _coordinate(latitude, 90) or not _coordinate(longitude, 180):
            return AssessmentResult.NOT_ESTABLISHED
        if rule.center_lat is not None and rule.center_lon is not None:
            if not _coordinate(rule.center_lat, 90) or not _coordinate(rule.center_lon, 180):
                return AssessmentResult.NOT_ESTABLISHED
            distance = _distance_km(latitude, longitude, rule.center_lat, rule.center_lon)
            return _result(distance <= rule.radius_km)
        if rule.polygon is not None:
            points = [(point[1], point[0]) for point in rule.polygon]
            return _result(_inside_polygon(latitude, longitude, points))
    return AssessmentResult.NOT_ESTABLISHED


def _result(value: bool) -> AssessmentResult:
    return AssessmentResult.MATCHES if value else AssessmentResult.DOES_NOT_MATCH


def _coordinate(value: Any, limit: float) -> bool:
    if type(value) not in (int, float) or not -limit <= value <= limit:
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi, delta_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def _inside_polygon(latitude: float, longitude: float, points: list[tuple[float, float]]) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        y1, x1 = previous
        y2, x2 = current
        if (y1 > latitude) != (y2 > latitude):
            crossing = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < crossing:
                inside = not inside
        previous = current
    return inside
