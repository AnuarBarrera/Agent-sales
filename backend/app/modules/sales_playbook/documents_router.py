from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.permissions import require_member
from app.db.session import get_db
from app.modules.identity.router import get_current_user
from app.modules.sales_playbook.document_service import SourceDocumentService
from app.modules.sales_playbook.documents import MAX_SOURCE_BYTES, DocumentNormalizationError

router = APIRouter(prefix="/sales-playbooks/source-documents", tags=["sales-playbook-sources"])


class SourceSectionRead(BaseModel):
    id: str
    heading: str
    heading_path: list[str]
    start_line: int
    end_line: int
    text: str


class SourceDocumentRead(BaseModel):
    id: UUID
    workspace_id: UUID
    original_filename: str
    mime_type: str
    original_size_bytes: int
    original_sha256: str
    normalized_format: str
    normalized_sha256: str
    normalization_status: str
    normalization_warnings: list[str]
    converter: str
    converter_version: str
    sections: list[SourceSectionRead]
    created_at: datetime


class SourceDocumentSelectionRead(BaseModel):
    id: UUID
    original_filename: str
    mime_type: str
    created_at: datetime
    original_sha256: str
    normalized_sha256: str
    section_count: int
    eligibility_status: Literal["eligible", "unavailable"]


def _service(db: Session) -> SourceDocumentService:
    return SourceDocumentService(db)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


async def _bounded_read(upload: UploadFile) -> bytes:
    blocks = []
    total = 0
    while block := await upload.read(min(1024 * 1024, MAX_SOURCE_BYTES + 1 - total)):
        total += len(block)
        if total > MAX_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="Document exceeds 20 MiB")
        blocks.append(block)
    return b"".join(blocks)


@router.post("", response_model=SourceDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_source_document(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, workspace_id, _ = current_user
    require_member(db, current_user[0], workspace_id)
    try:
        source = await _bounded_read(file)
        record = _service(db).upload(workspace_id, file.filename or "", file.content_type or "", source)
    except DocumentNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SourceDocumentRead.model_validate(record, from_attributes=True)


@router.get("/selection", response_model=list[SourceDocumentSelectionRead])
def list_source_documents(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _, workspace_id, _ = current_user
    require_member(db, current_user[0], workspace_id)
    records = _service(db).list(workspace_id)
    return [
        SourceDocumentSelectionRead(
            id=record.id,
            original_filename=record.original_filename,
            mime_type=record.mime_type,
            created_at=record.created_at,
            original_sha256=record.original_sha256,
            normalized_sha256=record.normalized_sha256,
            section_count=len(record.sections or []),
            eligibility_status=(
                "eligible"
                if record.normalization_status == "complete"
                and bool(record.sections)
                and _is_sha256(record.original_sha256)
                and _is_sha256(record.normalized_sha256)
                else "unavailable"
            ),
        )
        for record in records
    ]


@router.get("/{document_id}", response_model=SourceDocumentRead)
def get_source_document(document_id: UUID, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    _, workspace_id, _ = current_user
    require_member(db, current_user[0], workspace_id)
    record = _service(db).get(workspace_id, document_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Source document not found")
    return SourceDocumentRead.model_validate(record, from_attributes=True)
