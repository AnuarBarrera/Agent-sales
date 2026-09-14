from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"


class MessageDirection(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    INTERNAL = "internal_note"


class OutreachMessageBase(BaseModel):
    lead_id: UUID
    conversation_id: UUID | None = None
    direction: MessageDirection = MessageDirection.OUTBOUND
    channel: str = Field(..., min_length=1, max_length=50)
    subject: str | None = Field(None, max_length=255)
    body: str = Field(..., min_length=1)
    ai_generated: bool = False
    ai_model: str | None = None
    ai_prompt: str | None = None
    ai_context: str | None = None


class OutreachMessageCreate(OutreachMessageBase):
    status: MessageStatus = MessageStatus.DRAFT


class OutreachMessageUpdate(BaseModel):
    status: str | None = None
    subject: str | None = Field(None, max_length=255)
    body: str | None = None
    ai_generated: bool | None = None
    ai_model: str | None = None
    ai_prompt: str | None = None
    ai_context: str | None = None


class OutreachMessageRead(OutreachMessageBase):
    id: UUID
    workspace_id: UUID
    author_id: UUID | None
    status: str
    ai_generated: bool
    ai_model: str | None
    ai_prompt: str | None
    ai_context: str | None
    approved_by_id: UUID | None
    approved_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OutreachMessageListResponse(BaseModel):
    items: list["OutreachMessageRead"]
    next_cursor: str | None = None


class MessageDraftRequest(BaseModel):
    lead_id: UUID
    channel: str = Field(..., min_length=1, max_length=50)
    context: str | None = None
    tone: str | None = Field(None, max_length=50)
    length: str | None = Field(None, max_length=50)
    purpose: str | None = None


class MessageDraftResponse(BaseModel):
    draft: str
    message_id: UUID | None = None
    status: MessageStatus = MessageStatus.DRAFT
    ai_model: str
    tokens_used: int
    warnings: list[str] = []


class MessageApprovalRequest(BaseModel):
    approved: bool
    modified_body: str | None = None


class ConversationCreate(BaseModel):
    lead_id: UUID
    subject: str | None = Field(None, max_length=255)


class ConversationRead(BaseModel):
    id: UUID
    workspace_id: UUID
    lead_id: UUID
    subject: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
