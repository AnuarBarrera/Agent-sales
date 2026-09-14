from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.identity.router import get_current_workspace_id
from app.modules.rag.models import DocumentChunk
from app.modules.rag.schemas import (
    DocumentChunkCreate,
    DocumentChunkRead,
    RAGQueryRequest,
    RAGQueryResponse,
)
from app.modules.rag.service import RAGService

router = APIRouter(prefix="/rag", tags=["RAG"])

require_rag_member = require_role_dependency(["admin", "setter", "closer"])
require_rag_admin = require_role_dependency(["admin"])


@router.post("/chunks", response_model=list[DocumentChunkRead])
def create_chunks(
    chunk_data: DocumentChunkCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _current_user = Depends(require_rag_admin),
    db: Session = Depends(get_db),
):
    """Create document chunks with embeddings."""
    service = RAGService(db)
    chunks = service.add_document(
        workspace_id=workspace_id,
        source_type=chunk_data.source_type,
        source_id=chunk_data.source_id,
        content=chunk_data.content,
        metadata=chunk_data.metadata,
    )
    return [DocumentChunkRead.model_validate(c) for c in chunks]


@router.post("/query", response_model=RAGQueryResponse)
def query_rag(
    request: RAGQueryRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _current_user = Depends(require_rag_member),
    db: Session = Depends(get_db),
):
    """Query similar document chunks using vector similarity."""
    service = RAGService(db)
    return service.query(workspace_id, request)


@router.get("/chunks", response_model=list[DocumentChunkRead])
def list_chunks(
    source_type: str | None = Query(None),
    source_id: UUID | None = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _current_user = Depends(require_rag_member),
    db: Session = Depends(get_db),
):
    """List document chunks with optional filters."""
    query = select(DocumentChunk).where(DocumentChunk.workspace_id == workspace_id)

    if source_type:
        query = query.where(DocumentChunk.source_type == source_type)
    if source_id:
        query = query.where(DocumentChunk.source_id == source_id)

    query = query.order_by(DocumentChunk.created_at.desc()).limit(limit).offset(offset)
    chunks = list(db.execute(query).scalars().all())
    return [DocumentChunkRead.model_validate(c) for c in chunks]


@router.delete("/chunks")
def delete_chunks(
    source_type: str = Query(...),
    source_id: UUID = Query(...),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _current_user = Depends(require_rag_admin),
    db: Session = Depends(get_db),
):
    """Delete all chunks for a specific source."""
    service = RAGService(db)
    count = service.delete_chunks_by_source(workspace_id, source_type, source_id)
    return {"deleted": count}
