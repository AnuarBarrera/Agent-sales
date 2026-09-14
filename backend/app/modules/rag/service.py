import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.integrations.llm import LLMProvider
from app.modules.rag.models import DocumentChunk
from app.modules.rag.schemas import RAGQueryRequest, RAGQueryResponse

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, db: Session):
        self.db = db
        self.llm = LLMProvider()
        self._embedding_dim = 1536  # OpenAI text-embedding-3-small dimension

    def create_embedding(self, text: str) -> list[float]:
        """Create embedding for text using LLM provider."""
        try:
            return self.llm.embed(text)
        except Exception as e:
            logger.error(f"Failed to create embedding: {e}")
            raise RuntimeError("Embedding provider is unavailable") from e

    def add_document(
        self,
        workspace_id: UUID,
        source_type: str,
        source_id: UUID,
        content: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        metadata: dict | None = None,
    ) -> list[DocumentChunk]:
        """Split document into chunks, create embeddings, and store."""
        chunks = self._split_text(content, chunk_size, chunk_overlap)
        stored_chunks = []

        for i, chunk_text in enumerate(chunks):
            embedding = self.create_embedding(chunk_text)
            chunk = DocumentChunk(
                workspace_id=workspace_id,
                source_type=source_type,
                source_id=source_id,
                content=chunk_text,
                embedding=embedding,
                chunk_index=i,
                meta=metadata or {},
            )
            self.db.add(chunk)
            stored_chunks.append(chunk)

        self.db.flush()
        return stored_chunks

    def _split_text(self, text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        """Split text into overlapping chunks."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]

            # Try to break at sentence boundary
            if end < len(text):
                last_period = chunk.rfind(".")
                last_newline = chunk.rfind("\n")
                break_point = max(last_period, last_newline)
                if break_point > chunk_size // 2:
                    chunk = chunk[: break_point + 1]
                    end = start + break_point + 1

            chunks.append(chunk.strip())
            start = end - chunk_overlap

        return chunks

    def query(
        self,
        workspace_id: UUID,
        request: RAGQueryRequest,
    ) -> RAGQueryResponse:
        """Query similar chunks using vector similarity."""
        # Create embedding for query
        query_embedding = self.create_embedding(request.query)
        # Build query
        source_clause = ""
        params: dict[str, object] = {
            "workspace_id": str(workspace_id),
            "query_embedding": "[" + ",".join(str(x) for x in query_embedding) + "]",
            "threshold": request.similarity_threshold,
            "limit": request.limit,
        }
        if request.source_types:
            source_clause = " AND source_type = ANY(CAST(:source_types AS text[]))"
            params["source_types"] = request.source_types

        # Use pgvector cosine similarity
        similarity_query = text(f"""
            SELECT *, 1 - (embedding <=> CAST(:query_embedding AS vector)) as similarity
            FROM document_chunks
            WHERE workspace_id = :workspace_id
            AND embedding IS NOT NULL
            AND 1 - (embedding <=> CAST(:query_embedding AS vector)) >= :threshold
            {source_clause}
            ORDER BY embedding <=> CAST(:query_embedding AS vector)
            LIMIT :limit
        """)

        result = self.db.execute(
            similarity_query,
            params,
        ).fetchall()

        chunks = []
        for row in result:
            chunk = DocumentChunk(
                id=row.id,
                workspace_id=row.workspace_id,
                source_type=row.source_type,
                source_id=row.source_id,
                content=row.content,
                embedding=list(row.embedding) if row.embedding is not None else None,
                chunk_index=row.chunk_index,
                meta=row.meta,
                created_at=row.created_at,
            )
            chunks.append(chunk)

        return RAGQueryResponse(chunks=[self._chunk_to_read(c) for c in chunks], total=len(chunks))

    def _chunk_to_read(self, chunk: DocumentChunk):
        from app.modules.rag.schemas import DocumentChunkRead
        return DocumentChunkRead.model_validate(chunk)

    def index_call_transcript(self, workspace_id: UUID, transcript_id: UUID, content: str) -> list[DocumentChunk]:
        """Index a call transcript for RAG retrieval."""
        return self.add_document(
            workspace_id=workspace_id,
            source_type="transcript",
            source_id=transcript_id,
            content=content,
            metadata={"transcript_id": str(transcript_id)},
        )

    def index_outreach_message(self, workspace_id: UUID, message_id: UUID, content: str) -> list[DocumentChunk]:
        """Index an outreach message for RAG retrieval."""
        return self.add_document(
            workspace_id=workspace_id,
            source_type="outreach_message",
            source_id=message_id,
            content=content,
            metadata={"message_id": str(message_id)},
        )

    def index_activity(self, workspace_id: UUID, activity_id: UUID, content: str) -> list[DocumentChunk]:
        """Index an activity for RAG retrieval."""
        return self.add_document(
            workspace_id=workspace_id,
            source_type="activity",
            source_id=activity_id,
            content=content,
            metadata={"activity_id": str(activity_id)},
        )

    def get_chunks_by_source(self, workspace_id: UUID, source_type: str, source_id: UUID) -> list[DocumentChunk]:
        """Get all chunks for a specific source."""
        return list(
            self.db.execute(
                select(DocumentChunk)
                .where(
                    DocumentChunk.workspace_id == workspace_id,
                    DocumentChunk.source_type == source_type,
                    DocumentChunk.source_id == source_id,
                )
                .order_by(DocumentChunk.chunk_index)
            ).scalars().all()
        )

    def delete_chunks_by_source(self, workspace_id: UUID, source_type: str, source_id: UUID) -> int:
        """Delete all chunks for a specific source."""
        result = self.db.execute(
            select(DocumentChunk).where(
                DocumentChunk.workspace_id == workspace_id,
                DocumentChunk.source_type == source_type,
                DocumentChunk.source_id == source_id,
            )
        ).scalars().all()

        count = len(result)
        for chunk in result:
            self.db.delete(chunk)
        self.db.flush()
        return count
