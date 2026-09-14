from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ActivityType(str, Enum):
    NOTE = "note"
    CALL = "call"
    MESSAGE = "message"
    MEETING = "meeting"
    STAGE_CHANGE = "stage_change"
    ASSIGNMENT = "assignment"
    AI_INSIGHT = "ai_insight"
    TASK = "task"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ActivityBase(BaseModel):
    type: str = Field(..., description="Type of activity")
    content: str = Field(..., min_length=1, description="Activity content")
    related_id: UUID | None = None


class ActivityCreate(ActivityBase):
    lead_id: UUID


class ActivityRead(BaseModel):
    id: UUID
    workspace_id: UUID
    lead_id: UUID
    type: str
    content: str
    author_id: UUID
    related_id: UUID | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FollowUpTaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    due_at: datetime | None = None
    lead_id: UUID | None = None


class FollowUpTaskCreate(FollowUpTaskBase):
    assignee_id: UUID | None = None
    source_activity_id: UUID | None = None


class FollowUpTaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    assignee_id: UUID | None = None


class FollowUpTaskRead(FollowUpTaskBase):
    id: UUID
    workspace_id: UUID
    assignee_id: UUID
    created_by_id: UUID
    source_activity_id: UUID | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskListFilters(BaseModel):
    status: TaskStatus | None = None
    assignee_id: UUID | None = None
    due_before: datetime | None = None
    due_after: datetime | None = None
    cursor: str | None = None
    limit: int = Field(default=50, le=100)


class TaskListResponse(BaseModel):
    items: list[FollowUpTaskRead]
    next_cursor: str | None = None


class TimelineFilters(BaseModel):
    lead_id: UUID
    type: str | None = None
    cursor: str | None = None
    limit: int = Field(default=50, le=100)
