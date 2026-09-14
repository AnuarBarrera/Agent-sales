import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SellerProfile(Base):
    """The business that owns the sales workspace, not a prospect."""

    __tablename__ = "seller_profiles"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    offer_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_customer_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_or_market: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_context: Mapped[str | None] = mapped_column(String(500), nullable=True)
    primary_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    differentiators: Mapped[str | None] = mapped_column(Text, nullable=True)
    sales_constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (UniqueConstraint("workspace_id", name="uq_seller_profile_workspace"),)
