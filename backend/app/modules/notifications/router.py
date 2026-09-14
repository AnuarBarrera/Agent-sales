from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.notifications.schemas import (
    HandoffCreate,
    HandoffRead,
    HandoffResponse,
    NotificationListResponse,
    NotificationRead,
)
from app.modules.notifications.service import NotificationsService

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_notifications_service(db: Session = Depends(get_db)):
    return NotificationsService(db)

# Permission dependencies
require_notifications_view = require_role_dependency(["admin", "setter", "closer"])
require_handoff_manage = require_role_dependency(["admin", "setter", "closer"])


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    status: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, le=100),
    service: NotificationsService = Depends(get_notifications_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """List notifications for current user."""
    user_id_str, workspace_id_str, user_role = current_user
    workspace_id = workspace_id_str
    user_id = user_id_str

    notifications = service.get_notifications(
        workspace_id=workspace_id,
        user_id=user_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )

    unread_count = service.get_unread_count(workspace_id_str, user_id_str)

    return NotificationListResponse(
        items=[NotificationRead.model_validate(n) for n in notifications],
        unread_count=unread_count,
    )


@router.post("/{notification_id}/read")
def mark_as_read(
    notification_id: UUID,
    service: NotificationsService = Depends(get_notifications_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Mark a notification as read."""
    user_id_str, workspace_id_str, _ = current_user
    success = service.mark_as_read(
        notification_id=notification_id,
        user_id=user_id_str,
        workspace_id=workspace_id_str,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification marked as read"}


@router.post("/read-all")
def mark_all_as_read(
    service: NotificationsService = Depends(get_notifications_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Mark all notifications as read."""
    user_id_str, workspace_id_str, _ = current_user
    count = service.mark_all_as_read(
        workspace_id=workspace_id_str,
        user_id=user_id_str,
    )
    return {"message": f"Marked {count} notifications as read"}


@router.post("/{notification_id}/dismiss")
def dismiss_notification(
    notification_id: UUID,
    service: NotificationsService = Depends(get_notifications_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Dismiss a notification."""
    user_id_str, workspace_id_str, _ = current_user
    success = service.dismiss_notification(
        notification_id=notification_id,
        user_id=user_id_str,
        workspace_id=workspace_id_str,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification dismissed"}


# Handoff routes
@router.post("/handoffs", response_model=HandoffRead, status_code=status.HTTP_201_CREATED)
def create_handoff(
    handoff_data: HandoffCreate,
    service: NotificationsService = Depends(get_notifications_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Create a handoff (setter to closer)."""
    user_id_str, workspace_id_str, user_role = current_user
    try:
        handoff = service.create_handoff(
            workspace_id=workspace_id_str,
            lead_id=handoff_data.lead_id,
            from_user_id=user_id_str,
            to_user_id=handoff_data.to_user_id,
            reason=handoff_data.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HandoffRead.model_validate(handoff)


@router.get("/handoffs")
def list_handoffs(
    status: str | None = Query(None),
    service: NotificationsService = Depends(get_notifications_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """List handoffs for current user (as recipient)."""
    user_id_str, workspace_id_str, _ = current_user
    handoffs = service.get_handoffs_for_user(
        workspace_id=workspace_id_str,
        user_id=user_id_str,
        status=status,
    )
    return [HandoffRead.model_validate(h) for h in handoffs]


@router.post("/handoffs/{handoff_id}/respond", response_model=HandoffResponse)
def respond_to_handoff(
    handoff_id: UUID,
    accept: bool = Query(...),
    service: NotificationsService = Depends(get_notifications_service),
    current_user = Depends(require_role_dependency(["admin", "setter", "closer"])),
):
    """Accept or reject a handoff."""
    user_id_str, workspace_id_str, _ = current_user
    handoff = service.respond_to_handoff(
        handoff_id=handoff_id,
        workspace_id=workspace_id_str,
        user_id=user_id_str,
        accept=accept,
    )
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found or already responded")
    return HandoffResponse(
        status=handoff.status,
        responded_at=handoff.responded_at,
    )
