"""Application-owned identities for one new generic extraction run."""

from __future__ import annotations

import re

_UNIT_ID = re.compile(r"^generic_unit_[0-9]{3,}$")


def _identity(unit_id: str, kind: str, ordinal: int) -> str:
    if not _UNIT_ID.fullmatch(unit_id):
        raise ValueError("identity requires a deterministic generic unit ID")
    if ordinal < 1:
        raise ValueError("identity ordinal must be positive")
    return f"{unit_id}-{kind}-{ordinal:03d}"


def claim_id(unit_id: str, ordinal: int) -> str:
    return _identity(unit_id, "claim", ordinal)


def assertion_id(unit_id: str, ordinal: int) -> str:
    return _identity(unit_id, "assertion", ordinal)


def stage_id(unit_id: str, ordinal: int) -> str:
    return _identity(unit_id, "stage", ordinal)


def conflict_group_id(unit_id: str, ordinal: int) -> str:
    return _identity(unit_id, "conflict-group", ordinal)


def generic_node_id(ordinal: int) -> str:
    return _generic_identity("node", ordinal)


def generic_relation_id(ordinal: int) -> str:
    return _generic_identity("relation", ordinal)


def generic_conflict_id(ordinal: int) -> str:
    return _generic_identity("conflict", ordinal)


def generic_finding_id(ordinal: int) -> str:
    return _generic_identity("finding", ordinal)


def _generic_identity(kind: str, ordinal: int) -> str:
    if kind not in {"node", "relation", "conflict", "finding"}:
        raise ValueError("generic identity kind is unsupported")
    if ordinal < 1:
        raise ValueError("ordinal must be positive")
    return f"generic-{kind}-{ordinal:03d}"
