"""Safe metadata for post-parse generic extraction contract violations."""

from __future__ import annotations

import re
from collections.abc import Mapping

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:\[\]-]{1,192}$")
_EXPECTED = re.compile(r"^[A-Za-z0-9_ .:{},()\[\]#-]{1,256}$")
_SAFE_FIELDS = (
    "operation", "unit", "contract", "validation_code", "field",
    "received_value", "expected_constraint", "correlation_id",
)
_SOURCE_ALIAS = re.compile(r"^SRC_[0-9]{3}$")


def _safe_identifier(value: object, *, limit: int = 192) -> str | None:
    if not isinstance(value, str) or len(value) > limit or not _IDENTIFIER.fullmatch(value):
        return None
    return value


def build_contract_diagnostics(values: Mapping[str, object]) -> dict[str, str]:
    """Copy only allowlisted scalar identifiers; never retain source or response text."""
    result: dict[str, str] = {}
    for field in _SAFE_FIELDS:
        value = values.get(field)
        if field == "expected_constraint":
            if isinstance(value, str) and len(value) <= 256 and _EXPECTED.fullmatch(value):
                result[field] = value
            continue
        if field == "received_value":
            safe = _safe_identifier(value, limit=128)
            result[field] = safe if safe is not None else "<redacted>"
            continue
        safe = _safe_identifier(value)
        if safe is not None:
            result[field] = safe
    return result


class ExtractionContractValidationError(ValueError):
    """Provider content parsed, but violates a deterministic extraction contract."""

    def __init__(self, diagnostics: Mapping[str, object]):
        self.diagnostics = build_contract_diagnostics(diagnostics)
        super().__init__("generic extraction contract validation failed")


def build_provenance_diagnostics(values: Mapping[str, object]) -> dict[str, object]:
    """Normalize the existing provenance rejection details into the private safe envelope."""
    operation_id = values.get("operation_id")
    operation = _safe_identifier(operation_id)
    invalid = values.get("invalid_aliases")
    allowed = values.get("allowed_aliases")
    invalid_aliases = [
        value if isinstance(value, str) and _SOURCE_ALIAS.fullmatch(value) else "<redacted>"
        for value in invalid[:16]
    ] if isinstance(invalid, list) else []
    allowed_aliases = [
        value for value in allowed[:32]
        if isinstance(value, str) and _SOURCE_ALIAS.fullmatch(value)
    ] if isinstance(allowed, list) else []
    result: dict[str, object] = {
        **build_contract_diagnostics({
            "operation": operation,
            "unit": operation.rsplit(".", 1)[-1] if operation else None,
            "contract": "generic_extraction_provenance_v1",
            "validation_code": "source_alias_not_authorized",
            "field": "source_refs",
            "received_value": invalid_aliases[0] if invalid_aliases else "<redacted>",
            "expected_constraint": "source_refs must be authorized for the current extraction operation",
        }),
        "invalid_aliases": invalid_aliases,
        "allowed_aliases": allowed_aliases,
    }
    assertion_index = values.get("assertion_index")
    if isinstance(assertion_index, int) and assertion_index >= 0:
        result["assertion_index"] = assertion_index
    assertion_id_value = _safe_identifier(values.get("assertion_id"), limit=160)
    if assertion_id_value is not None:
        result["assertion_id"] = assertion_id_value
    return result
