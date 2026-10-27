"""Deterministic, business-agnostic partitioning of normalized source material."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from app.modules.sales_playbook.extraction import map_markdown_sections

GENERIC_UNIT_MAX_SOURCE_BYTES = 6_000
GENERIC_UNIT_MAX_SECTION_IDS = 27


@dataclass(frozen=True)
class ExtractionSourceSection:
    """A normalized section with its immutable document and line provenance."""

    document_id: str | None
    section_id: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    order: int
    text: str
    start_byte_offset: int = 0
    end_byte_offset: int | None = None
    fragment_id: str = ""

    def __post_init__(self) -> None:
        end = self.end_byte_offset
        if end is None:
            end = self.start_byte_offset + len(self.text.encode("utf-8"))
            object.__setattr__(self, "end_byte_offset", end)
        if not self.fragment_id:
            identity = "\0".join((
                self.document_id or "", self.section_id,
                str(self.start_byte_offset), str(end),
                sha256(self.text.encode("utf-8")).hexdigest(),
            ))
            object.__setattr__(self, "fragment_id", f"frag_{sha256(identity.encode()).hexdigest()[:32]}")


@dataclass(frozen=True)
class GenericExtractionUnit:
    """A bounded source-order group. It carries no business-owner semantics."""

    unit_id: str
    source_sections: tuple[ExtractionSourceSection, ...]
    source_bytes: int


def source_sections_from_markdown(
    markdown: str, *, document_id: str | None = None,
) -> tuple[ExtractionSourceSection, ...]:
    """Create stable generic sections from normalized Markdown and 1-based lines."""

    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("source document contains no usable text")

    lines = markdown.splitlines()
    mapped = map_markdown_sections(markdown)
    if not mapped:
        raise ValueError("source document contains no usable sections")

    heading_lines = [
        index for index, line in enumerate(lines)
        if re.match(r"^#{1,6}\s+.+?\s*$", line)
    ]
    if heading_lines and len(heading_lines) != len(mapped):
        raise ValueError("normalized source section mapping is inconsistent")

    result: list[ExtractionSourceSection] = []
    for index, section in enumerate(mapped):
        start_index = heading_lines[index] if heading_lines else 0
        end_index = (
            heading_lines[index + 1] - 1
            if heading_lines and index + 1 < len(heading_lines)
            else len(lines) - 1
        )
        if end_index < start_index:
            raise ValueError("normalized source section has an invalid line range")
        text = "\n".join(lines[start_index:end_index + 1])
        if not text.strip():
            raise ValueError("normalized source section contains no usable text")
        result.append(ExtractionSourceSection(
            document_id=document_id,
            section_id=section.id,
            heading_path=section.path,
            start_line=start_index + 1,
            end_line=end_index + 1,
            order=section.order,
            text=text,
        ))
    return tuple(result)


def generic_source_sections(
    documents: Sequence[dict[str, str | None]],
) -> tuple[ExtractionSourceSection, ...]:
    """Build the one canonical ordered source representation used by planner and review map."""
    result: list[ExtractionSourceSection] = []
    for document_index, document in enumerate(documents):
        content = document.get("content")
        if not isinstance(content, str):
            raise ValueError("source document content is unavailable")
        document_id = document.get("document_id")
        sections = source_sections_from_markdown(content, document_id=document_id)
        for section in sections:
            section_id = (
                f"doc:{document_id}:{section.section_id}" if document_id
                else f"source:{document_index}:{section.section_id}"
            )
            result.append(ExtractionSourceSection(
                document_id=document_id,
                section_id=section_id,
                heading_path=section.heading_path,
                start_line=section.start_line,
                end_line=section.end_line,
                order=len(result),
                text=section.text,
                start_byte_offset=section.start_byte_offset,
                end_byte_offset=section.end_byte_offset,
            ))
    return tuple(result)


def build_generic_extraction_plan(
    sections: Sequence[ExtractionSourceSection],
    *,
    max_source_bytes: int = GENERIC_UNIT_MAX_SOURCE_BYTES,
) -> tuple[GenericExtractionUnit, ...]:
    """Pack source-order section slices without assigning semantic responsibility."""

    if max_source_bytes < 1:
        raise ValueError("max_source_bytes must be positive")
    if not sections:
        raise ValueError("source document contains no usable sections")
    if any(not section.text.strip() for section in sections):
        raise ValueError("source document contains an empty section")
    if len({(section.document_id, section.section_id, section.order) for section in sections}) != len(sections):
        raise ValueError("source section identities must be unique")

    ordered = sorted(sections, key=lambda item: (item.order, item.document_id or "", item.section_id))
    slices = [
        source_slice
        for section in ordered
        for source_slice in _bounded_section_slices(section, max_source_bytes)
    ]

    units: list[GenericExtractionUnit] = []
    current: list[ExtractionSourceSection] = []
    current_bytes = 0
    current_ids: set[tuple[str | None, str]] = set()

    def flush() -> None:
        nonlocal current, current_bytes, current_ids
        if current:
            units.append(GenericExtractionUnit(
                unit_id=f"generic_unit_{len(units) + 1:03d}",
                source_sections=tuple(current),
                source_bytes=current_bytes,
            ))
        current = []
        current_bytes = 0
        current_ids = set()

    for source_slice in slices:
        size = len(source_slice.text.encode("utf-8"))
        key = (source_slice.document_id, source_slice.section_id)
        if current and (
            current_bytes + size > max_source_bytes
            or (key not in current_ids and len(current_ids) >= GENERIC_UNIT_MAX_SECTION_IDS)
        ):
            flush()
        current.append(source_slice)
        current_bytes += size
        current_ids.add(key)
    flush()
    return tuple(units)


def _bounded_section_slices(
    section: ExtractionSourceSection, max_source_bytes: int,
) -> tuple[ExtractionSourceSection, ...]:
    """Prefer paragraph boundaries, then lines, then Unicode codepoints."""

    lines = section.text.splitlines(keepends=True)
    if not lines:
        lines = [section.text]
    output: list[ExtractionSourceSection] = []
    current_parts: list[str] = []
    current_bytes = 0
    current_start = section.start_line
    current_end = section.start_line
    current_start_byte = section.start_byte_offset
    current_end_byte = section.start_byte_offset

    def emit() -> None:
        nonlocal current_parts, current_bytes, current_start, current_end
        nonlocal current_start_byte, current_end_byte
        if current_parts:
            output.append(section.__class__(
                document_id=section.document_id,
                section_id=section.section_id,
                heading_path=section.heading_path,
                start_line=current_start,
                end_line=current_end,
                order=section.order,
                text="".join(current_parts),
                start_byte_offset=current_start_byte,
                end_byte_offset=current_end_byte,
            ))
        current_parts = []
        current_bytes = 0

    def emit_oversized_line(line: str, line_number: int, line_start_byte: int) -> None:
        emit()
        fragment = ""
        fragment_bytes = 0
        fragment_start_byte = line_start_byte
        byte_cursor = line_start_byte
        for character in line:
            character_bytes = len(character.encode("utf-8"))
            if fragment and fragment_bytes + character_bytes > max_source_bytes:
                output.append(section.__class__(
                    document_id=section.document_id,
                    section_id=section.section_id,
                    heading_path=section.heading_path,
                    start_line=line_number,
                    end_line=line_number,
                    order=section.order,
                    text=fragment,
                    start_byte_offset=fragment_start_byte,
                    end_byte_offset=byte_cursor,
                ))
                fragment = ""
                fragment_bytes = 0
                fragment_start_byte = byte_cursor
            fragment += character
            fragment_bytes += character_bytes
            byte_cursor += character_bytes
        if fragment:
            output.append(section.__class__(
                document_id=section.document_id,
                section_id=section.section_id,
                heading_path=section.heading_path,
                start_line=line_number,
                end_line=line_number,
                order=section.order,
                text=fragment,
                start_byte_offset=fragment_start_byte,
                end_byte_offset=byte_cursor,
            ))

    # Keep each blank separator attached to the paragraph immediately before
    # it, allowing adjacent small paragraphs to share a unit where they fit.
    paragraphs: list[tuple[int, list[tuple[int, str, int, int]]]] = []
    paragraph_start = section.start_line
    paragraph_lines: list[tuple[int, str, int, int]] = []
    byte_cursor = section.start_byte_offset
    for offset, line in enumerate(lines):
        line_number = section.start_line + offset
        line_start_byte = byte_cursor
        byte_cursor += len(line.encode("utf-8"))
        if not paragraph_lines:
            paragraph_start = line_number
        paragraph_lines.append((line_number, line, line_start_byte, byte_cursor))
        if not line.strip():
            paragraphs.append((paragraph_start, paragraph_lines))
            paragraph_lines = []
    if paragraph_lines:
        paragraphs.append((paragraph_start, paragraph_lines))

    for paragraph_start, paragraph in paragraphs:
        paragraph_bytes = sum(len(line.encode("utf-8")) for _, line, _, _ in paragraph)
        if current_parts and current_bytes + paragraph_bytes > max_source_bytes:
            emit()
        if paragraph_bytes <= max_source_bytes:
            if not current_parts:
                current_start = paragraph_start
                current_start_byte = paragraph[0][2]
            current_parts.extend(line for _, line, _, _ in paragraph)
            current_bytes += paragraph_bytes
            current_end = paragraph[-1][0]
            current_end_byte = paragraph[-1][3]
            continue

        # If a paragraph itself is too large, fall back to normalized lines.
        for line_number, line, line_start_byte, line_end_byte in paragraph:
            line_size = len(line.encode("utf-8"))
            if line_size > max_source_bytes:
                emit_oversized_line(line, line_number, line_start_byte)
                continue
            if current_parts and current_bytes + line_size > max_source_bytes:
                emit()
            if not current_parts:
                current_start = line_number
                current_start_byte = line_start_byte
            current_parts.append(line)
            current_bytes += line_size
            current_end = line_number
            current_end_byte = line_end_byte
    emit()
    return tuple(output)


__all__ = [
    "GENERIC_UNIT_MAX_SECTION_IDS",
    "GENERIC_UNIT_MAX_SOURCE_BYTES",
    "ExtractionSourceSection",
    "GenericExtractionUnit",
    "build_generic_extraction_plan",
    "generic_source_sections",
    "source_sections_from_markdown",
]
