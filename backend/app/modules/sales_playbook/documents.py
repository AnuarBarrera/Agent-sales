"""Deterministic normalization and sectioning for untrusted sales documents."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath

MAX_SOURCE_BYTES = 20 * 1024 * 1024
NORMALIZATION_TIMEOUT_SECONDS = 30
SUPPORTED = {
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".odt": {"application/vnd.oasis.opendocument.text"},
    ".html": {"text/html", "application/xhtml+xml"},
    ".htm": {"text/html", "application/xhtml+xml"},
}


class DocumentNormalizationError(ValueError):
    """An uploaded source cannot be normalized safely and completely."""


@dataclass(frozen=True)
class SourceSection:
    id: str
    heading: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True)
class NormalizedDocument:
    content: str
    sha256: str
    normalized_format: str
    converter: str
    converter_version: str
    warnings: tuple[str, ...]
    sections: tuple[SourceSection, ...]


def sanitize_filename(filename: str) -> str:
    leaf = PurePath(filename.replace("\\", "/")).name
    leaf = unicodedata.normalize("NFKC", leaf)
    leaf = re.sub(r"[^\w.() -]+", "_", leaf, flags=re.UNICODE).strip(" .")
    if not leaf or leaf in {".", ".."}:
        raise DocumentNormalizationError("filename is invalid")
    return leaf[:180]


class DocumentNormalizer:
    """Normalize Markdown/text directly and office/HTML through Pandoc."""

    def __init__(self, *, max_bytes: int = MAX_SOURCE_BYTES, timeout: int = NORMALIZATION_TIMEOUT_SECONDS):
        self.max_bytes = max_bytes
        self.timeout = timeout

    def normalize(self, filename: str, mime_type: str, source: bytes) -> NormalizedDocument:
        safe_name = sanitize_filename(filename)
        extension = PurePath(safe_name).suffix.lower()
        if extension not in SUPPORTED:
            raise DocumentNormalizationError("unsupported document format")
        if mime_type not in SUPPORTED[extension]:
            raise DocumentNormalizationError("document extension and MIME type do not match")
        if not source:
            raise DocumentNormalizationError("document is empty")
        if len(source) > self.max_bytes:
            raise DocumentNormalizationError("document exceeds the upload size limit")

        warnings: tuple[str, ...] = ()
        if extension in {".md", ".txt"}:
            try:
                content = source.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise DocumentNormalizationError("text document must be valid UTF-8") from exc
            converter, version = "passthrough", "1"
        else:
            content, version, warnings = self._pandoc(source, extension)
            converter = "pandoc"

        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if not content.strip():
            raise DocumentNormalizationError("normalized document is empty")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise DocumentNormalizationError("normalized document exceeds the size limit")
        digest = hashlib.sha256(encoded).hexdigest()
        return NormalizedDocument(
            content=content,
            sha256=digest,
            normalized_format="markdown",
            converter=converter,
            converter_version=version,
            warnings=warnings,
            sections=tuple(_sections(content)),
        )

    def _pandoc(self, source: bytes, extension: str) -> tuple[str, str, tuple[str, ...]]:
        source_format = {".docx": "docx", ".odt": "odt", ".html": "html", ".htm": "html"}[extension]
        deadline = time.monotonic() + self.timeout

        def remaining_timeout() -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("pandoc", self.timeout)
            return remaining

        try:
            version_result = subprocess.run(
                ["pandoc", "--version"], input=None, capture_output=True, text=True,
                timeout=remaining_timeout(), check=False,
            )
            if version_result.returncode:
                raise DocumentNormalizationError("Pandoc version check failed")
            version = (version_result.stdout.splitlines() or ["unknown"])[0].strip()
            result = subprocess.run(
                ["pandoc", "--from", source_format, "--to", "gfm"], input=source,
                capture_output=True, timeout=remaining_timeout(), check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentNormalizationError("document normalization timed out") from exc
        except FileNotFoundError as exc:
            raise DocumentNormalizationError("Pandoc is required for this document format") from exc
        if result.returncode:
            raise DocumentNormalizationError(f"Pandoc conversion failed (exit code {result.returncode})")
        try:
            content = result.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentNormalizationError("Pandoc returned invalid UTF-8") from exc
        warning_text = result.stderr.decode("utf-8", errors="replace")
        warnings = tuple(line.strip()[:500] for line in warning_text.splitlines() if line.strip())
        return content, version[:100], warnings


def _sections(content: str) -> list[SourceSection]:
    # Reuse the exact extraction section-ID contract so an uploaded source
    # citation resolves to the same section identifier as model output.
    from app.modules.sales_playbook.extraction import map_markdown_sections

    lines = content.splitlines()
    heading_lines: list[int] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            heading_lines.append(index)
    mapped = map_markdown_sections(content)
    result: list[SourceSection] = []
    for position, section in enumerate(mapped):
        start = heading_lines[position] + 1 if heading_lines else 1
        end = heading_lines[position + 1] if heading_lines and position + 1 < len(heading_lines) else max(start, len(lines))
        text = "\n".join(lines[start - 1:end]).strip()
        result.append(SourceSection(
            section.id, section.heading, section.path, start, end, text or section.heading,
        ))
    return result
