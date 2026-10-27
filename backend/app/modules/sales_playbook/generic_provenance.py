"""Operation-local evidence aliases and deterministic source resolution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.modules.sales_playbook.source_planning import ExtractionSourceSection


@dataclass(frozen=True)
class LocalSourceReference:
    """An LLM-visible alias mapped privately to one exact normalized fragment."""

    alias: str
    fragment_id: str
    document_id: str | None
    section_id: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    start_byte_offset: int
    end_byte_offset: int


class ProvenanceValidationError(ValueError):
    """An output cited a reference not authorized for its extraction operation."""

    def __init__(
        self,
        *,
        operation_id: str,
        invalid_aliases: Sequence[str],
        allowed_aliases: Sequence[str],
        assertion_index: int | None = None,
        assertion_id: str | None = None,
    ) -> None:
        self.diagnostics = {
            "category": "provenance_validation",
            "operation_id": operation_id,
            "invalid_aliases": list(dict.fromkeys(
                value[:128] if isinstance(value, str) and value.isprintable() else "<invalid-format>"
                for value in invalid_aliases
            )),
            "allowed_aliases": list(allowed_aliases),
            "assertion_index": assertion_index,
            "assertion_id": assertion_id[:160] if isinstance(assertion_id, str) else None,
        }
        super().__init__("provenance alias is outside the extraction operation")


def build_local_source_reference_map(
    source_sections: Sequence[ExtractionSourceSection],
) -> tuple[LocalSourceReference, ...]:
    """Assign stable-in-order aliases scoped only to this provider operation."""

    fragment_ids = [section.fragment_id for section in source_sections]
    if not source_sections or len(fragment_ids) != len(set(fragment_ids)):
        raise ValueError("an extraction operation requires unique source fragments")
    return tuple(
        LocalSourceReference(
            alias=f"SRC_{index:03d}",
            fragment_id=section.fragment_id,
            document_id=section.document_id,
            section_id=section.section_id,
            heading_path=section.heading_path,
            start_line=section.start_line,
            end_line=section.end_line,
            start_byte_offset=section.start_byte_offset,
            end_byte_offset=section.end_byte_offset or 0,
        )
        for index, section in enumerate(source_sections, start=1)
    )


def resolve_local_source_references(
    returned_aliases: Sequence[Any],
    references: Sequence[LocalSourceReference],
    *,
    operation_id: str,
    assertion_index: int | None = None,
    assertion_id: str | None = None,
) -> list[str]:
    """Fail closed and resolve only aliases belonging to this operation."""

    by_alias = {reference.alias: reference for reference in references}
    invalid_values = [
        alias for alias in returned_aliases
        if not isinstance(alias, str) or alias not in by_alias
    ]
    invalid = [
        alias[:128] if isinstance(alias, str) and alias.isprintable() else "<invalid-format>"
        for alias in invalid_values
    ]
    if not returned_aliases or invalid:
        raise ProvenanceValidationError(
            operation_id=operation_id,
            invalid_aliases=invalid or ["<empty>"],
            allowed_aliases=tuple(by_alias),
            assertion_index=assertion_index,
            assertion_id=assertion_id,
        )
    return list(dict.fromkeys(by_alias[alias].fragment_id for alias in returned_aliases))


__all__ = [
    "LocalSourceReference",
    "ProvenanceValidationError",
    "build_local_source_reference_map",
    "resolve_local_source_references",
]
