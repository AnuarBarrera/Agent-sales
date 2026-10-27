import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceDocument(Base):
    """Immutable uploaded source and its deterministic normalized derivative."""

    __tablename__ = "sales_playbook_source_documents"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(180), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    original_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_format: Mapped[str] = mapped_column(String(40), nullable=False)
    normalized_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_status: Mapped[str] = mapped_column(String(30), nullable=False)
    normalization_warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    converter: Mapped[str] = mapped_column(String(80), nullable=False)
    converter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    sections: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_sales_playbook_source_workspace_id"),
    )
