from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentChunkBase(BaseModel):
    source_type: str
    source_id: UUID
    content: str
    embedding: list[float] | None = None
    chunk_index: int
    metadata: dict | None = Field(default=None, validation_alias="meta", serialization_alias="metadata")


class DocumentChunkCreate(DocumentChunkBase):
    pass


class DocumentChunkRead(DocumentChunkBase):
    id: UUID
    workspace_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RAGQueryRequest(BaseModel):
    query: str
    source_types: list[str] | None = None
    limit: int = 10
    similarity_threshold: float = 0.7


class RAGQueryResponse(BaseModel):
    chunks: list[DocumentChunkRead]
    total: int
