from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationType(str, Enum):
    TASK_DUE_SOON = "task_due_soon"
    TASK_OVERDUE = "task_overdue"
    HANDOFF_RECEIVED = "handoff_received"
    LEAD_ASSIGNED = "lead_assigned"
    STAGE_CHANGED = "stage_changed"
    MENTION = "mention"
    COMMENT = "comment"
    CALL_SCHEDULED = "call_scheduled"


class NotificationStatus(str, Enum):
    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"


class HandoffStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class NotificationRead(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    type: str
    title: str
    message: str
    source_id: UUID | None
    source_type: str | None
    status: NotificationStatus
    read_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    items: list["NotificationRead"]
    unread_count: int
    next_cursor: str | None = None


class HandoffCreate(BaseModel):
    lead_id: UUID
    to_user_id: UUID
    reason: str | None = Field(None, max_length=500)


class HandoffRead(BaseModel):
    id: UUID
    workspace_id: UUID
    lead_id: UUID
    from_user_id: UUID
    to_user_id: UUID
    reason: str | None
    status: HandoffStatus
    initiated_at: datetime
    responded_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class HandoffResponse(BaseModel):
    status: HandoffStatus
    responded_at: datetime | None
