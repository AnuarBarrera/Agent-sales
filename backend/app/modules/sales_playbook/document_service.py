from __future__ import annotations

import hashlib
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.storage import StorageProvider
from app.modules.sales_playbook.document_models import SourceDocument
from app.modules.sales_playbook.documents import (
    DocumentNormalizer,
    sanitize_filename,
)


class SourceDocumentService:
    def __init__(self, db: Session, storage: StorageProvider | None = None,
                 normalizer: DocumentNormalizer | None = None):
        self.db = db
        self.storage = storage
        self.normalizer = normalizer or DocumentNormalizer()

    def upload(self, workspace_id: UUID, filename: str, mime_type: str, source: bytes) -> SourceDocument:
        safe_name = sanitize_filename(filename)
        normalized = self.normalizer.normalize(safe_name, mime_type, source)
        storage = self.storage or StorageProvider()
        document_id = uuid4()
        prefix = f"sales-playbook-sources/{workspace_id}/{document_id}"
        original_key = f"{prefix}/original/{safe_name}"
        normalized_key = f"{prefix}/normalized.md"
        original_sha = hashlib.sha256(source).hexdigest()
        try:
            storage.upload(original_key, source, mime_type)
            storage.upload(normalized_key, normalized.content.encode("utf-8"), "text/markdown; charset=utf-8")
            record = SourceDocument(
                id=document_id, workspace_id=workspace_id, original_filename=safe_name,
                mime_type=mime_type, original_size_bytes=len(source), original_object_key=original_key,
                original_sha256=original_sha, normalized_format=normalized.normalized_format,
                normalized_object_key=normalized_key, normalized_sha256=normalized.sha256,
                normalization_status="complete", normalization_warnings=list(normalized.warnings),
                converter=normalized.converter, converter_version=normalized.converter_version,
                sections=[_section_dump(section) for section in normalized.sections],
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            )
            self.db.add(record)
            self.db.flush()
            self.db.commit()
            return record
        except Exception:
            self.db.rollback()
            for key in (normalized_key, original_key):
                with suppress(Exception):
                    storage.delete(key)
                    # Preserve the persistence/conversion error; an orphan is
                    # private and can be reconciled without losing evidence.
            raise

    def list(self, workspace_id: UUID) -> list[SourceDocument]:
        return list(self.db.execute(
            select(SourceDocument).where(SourceDocument.workspace_id == workspace_id)
            .order_by(SourceDocument.created_at.desc())
        ).scalars().all())

    def get(self, workspace_id: UUID, document_id: UUID) -> SourceDocument | None:
        return self.db.execute(select(SourceDocument).where(
            SourceDocument.workspace_id == workspace_id, SourceDocument.id == document_id,
        )).scalar_one_or_none()

    def normalized_content(self, workspace_id: UUID, document_id: UUID) -> tuple[SourceDocument, str] | None:
        record = self.get(workspace_id, document_id)
        if record is None:
            return None
        storage = self.storage or StorageProvider()
        return record, storage.download(record.normalized_object_key).decode("utf-8")


def _section_dump(section) -> dict:
    return {
        "id": section.id, "heading": section.heading, "heading_path": list(section.heading_path),
        "start_line": section.start_line, "end_line": section.end_line, "text": section.text,
    }
