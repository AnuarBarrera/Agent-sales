"""Durable records for OTP challenges, demo participants, and admissions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DemoParticipant(Base):
    __tablename__ = "demo_participants"
    __table_args__ = (
        CheckConstraint("search_limit = 2", name="ck_demo_participants_search_limit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_key: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    search_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class DemoDraftApproval(Base):
    """Human approval of one exact draft snapshot for manual use."""

    __tablename__ = "demo_draft_approvals"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("demo_participants.id", ondelete="RESTRICT"), nullable=False
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("setter_executions.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    draft_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class DemoOtpChallenge(Base):
    __tablename__ = "demo_otp_challenges"
    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_demo_otp_attempts"),
        CheckConstraint(
            "delivery_status IN ('pending', 'sent', 'failed')", name="ck_demo_otp_delivery_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_key: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    code_hmac: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class DemoSearchReservation(Base):
    __tablename__ = "demo_search_reservations"
    __table_args__ = (
        UniqueConstraint("participant_id", "key_hash", name="uq_demo_search_participant_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("demo_participants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prospection_runs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class DemoDraftReservation(Base):
    __tablename__ = "demo_draft_reservations"
    __table_args__ = (
        UniqueConstraint("participant_id", "lead_id", name="uq_demo_draft_participant_lead"),
        CheckConstraint("channel IN ('email', 'phone')", name="ck_demo_draft_channel"),
        CheckConstraint("status IN ('reserved', 'dispatched', 'failed')", name="ck_demo_draft_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("demo_participants.id", ondelete="RESTRICT"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("setter_executions.id", ondelete="RESTRICT")
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved", server_default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
