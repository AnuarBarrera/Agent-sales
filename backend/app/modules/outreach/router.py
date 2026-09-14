from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.outreach.schemas import (
    ConversationCreate,
    ConversationRead,
    MessageApprovalRequest,
    MessageDraftRequest,
    MessageDraftResponse,
    OutreachMessageCreate,
    OutreachMessageListResponse,
    OutreachMessageRead,
)
from app.modules.outreach.service import OutreachService

router = APIRouter(prefix="/outreach", tags=["outreach"])


def get_outreach_service(db: Session = Depends(get_db)):
    return OutreachService(db)

# Permission dependencies
require_outreach_view = require_role_dependency(["admin", "setter", "closer"])
require_outreach_manage = require_role_dependency(["admin", "closer"])


@router.post("/message-draft", response_model=MessageDraftResponse, status_code=status.HTTP_201_CREATED)
def create_message_draft(
    request: MessageDraftRequest,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Generate an AI message draft."""
    user_id_str, workspace_id_str, user_role = current_user
    try:
        result = service.create_draft(
            workspace_id=workspace_id_str,
            lead_id=request.lead_id,
            user_id=user_id_str,
            channel=request.channel,
            context=request.context,
            tone=request.tone,
            length=request.length,
            purpose=request.purpose,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating draft: {str(e)}") from e


@router.post("/messages", response_model=OutreachMessageRead, status_code=status.HTTP_201_CREATED)
def create_message(
    message_data: OutreachMessageCreate,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_outreach_manage),
):
    """Create a message (draft by default)."""
    user_id_str, workspace_id_str, user_role = current_user
    message = service.create_message(
        workspace_id=workspace_id_str,
        lead_id=message_data.lead_id,
        user_id=user_id_str,
        conversation_id=message_data.conversation_id,
        direction=message_data.direction,
        channel=message_data.channel,
        subject=message_data.subject,
        body=message_data.body,
        ai_generated=message_data.ai_generated,
        ai_model=message_data.ai_model,
        ai_prompt=message_data.ai_prompt,
        ai_context=message_data.ai_context,
        status=message_data.status,
    )
    return OutreachMessageRead.model_validate(message)


@router.get("/leads/{lead_id}/messages", response_model=OutreachMessageListResponse)
def list_lead_messages(
    lead_id: UUID,
    limit: int = Query(50, le=100),
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Get all messages for a lead."""
    _, workspace_id_str, _ = current_user
    messages = service.get_lead_messages(
        lead_id=lead_id,
        workspace_id=workspace_id_str,
        limit=limit,
    )
    return OutreachMessageListResponse(items=[OutreachMessageRead.model_validate(m) for m in messages])


@router.get("/messages/{message_id}", response_model=OutreachMessageRead)
def get_message(
    message_id: UUID,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Get a message by ID."""
    _, workspace_id_str, _ = current_user
    message = service.get_message(message_id, workspace_id_str)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    return OutreachMessageRead.model_validate(message)


@router.post("/messages/{message_id}/approve", response_model=OutreachMessageRead)
def approve_message(
    message_id: UUID,
    approval: MessageApprovalRequest,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "closer"])),
):
    """Approve a draft message."""
    _, workspace_id_str, _ = current_user
    message = service.approve_message(
        message_id=message_id,
        workspace_id=workspace_id_str,
        approver_id=current_user[0],
        modified_body=approval.modified_body,
    )
    if not message:
        raise HTTPException(status_code=404, detail="Message not found or not a draft")
    return OutreachMessageRead.model_validate(message)


@router.post("/messages/{message_id}/reject", response_model=OutreachMessageRead)
def reject_message(
    message_id: UUID,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "closer"])),
):
    """Reject a draft message."""
    _, workspace_id_str, _ = current_user
    message = service.reject_message(
        message_id=message_id,
        workspace_id=workspace_id_str,
        approver_id=current_user[0],
    )
    if not message:
        raise HTTPException(status_code=404, detail="Message not found or not a draft")
    return OutreachMessageRead.model_validate(message)


@router.post("/messages/{message_id}/mark-sent", response_model=OutreachMessageRead)
def mark_message_sent(
    message_id: UUID,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "closer"])),
):
    """Mark an approved message as sent."""
    _, workspace_id_str, _ = current_user
    message = service.mark_sent(
        message_id=message_id,
        workspace_id=workspace_id_str,
    )
    if not message:
        raise HTTPException(status_code=404, detail="Message not found or not approved")
    return OutreachMessageRead.model_validate(message)


# Conversation endpoints
@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    conversation_data: ConversationCreate,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Create a new conversation."""
    _, workspace_id_str, _ = current_user
    conversation = service.create_conversation(
        workspace_id=workspace_id_str,
        lead_id=conversation_data.lead_id,
        subject=conversation_data.subject,
    )
    return ConversationRead.model_validate(conversation)


@router.get("/leads/{lead_id}/conversations", response_model=list[ConversationRead])
def list_conversations(
    lead_id: UUID,
    limit: int = Query(50, le=100),
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """List conversations for a lead."""
    _, workspace_id_str, _ = current_user
    conversations = service.list_conversations(
        workspace_id=workspace_id_str,
        lead_id=lead_id,
        limit=limit,
    )
    return [ConversationRead.model_validate(c) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(
    conversation_id: UUID,
    service: OutreachService = Depends(get_outreach_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Get a conversation by ID."""
    _, workspace_id_str, _ = current_user
    conversation = service.get_conversation(conversation_id, workspace_id_str)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationRead.model_validate(conversation)
