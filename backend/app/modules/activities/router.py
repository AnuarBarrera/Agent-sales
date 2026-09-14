from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.activities.schemas import (
    ActivityCreate,
    ActivityRead,
    FollowUpTaskCreate,
    FollowUpTaskRead,
    FollowUpTaskUpdate,
)
from app.modules.activities.service import ActivitiesService
from app.modules.identity.models import UserRole

router = APIRouter(prefix="/activities", tags=["activities"])

def get_activities_service(db: Session = Depends(get_db)) -> ActivitiesService:
    return ActivitiesService(db)

# Permission dependencies
require_lead_view = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])
require_lead_manage = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])


@router.post("", response_model=ActivityRead, status_code=status.HTTP_201_CREATED)
def create_activity(
    activity_data: ActivityCreate,
    service: ActivitiesService = Depends(get_activities_service),
    current_user: tuple[str, str, str] = Depends(require_lead_manage),
) -> ActivityRead:
    """Create a new activity."""
    user_id_str, workspace_id_str, user_role = current_user
    activity = service.create_activity(
        workspace_id=workspace_id_str,
        lead_id=activity_data.lead_id,
        activity_type=activity_data.type,
        content=activity_data.content,
        author_id=user_id_str,
        related_id=activity_data.related_id,
    )
    return ActivityRead.model_validate(activity)


@router.post("/notes", response_model=ActivityRead, status_code=status.HTTP_201_CREATED)
def create_note(
    lead_id: UUID,
    content: str,
    service: ActivitiesService = Depends(get_activities_service),
    current_user: tuple[str, str, str] = Depends(require_lead_manage),
) -> ActivityRead:
    """Create a note activity for a lead."""
    user_id_str, workspace_id_str, user_role = current_user
    activity = service.create_note(
        workspace_id=workspace_id_str,
        lead_id=lead_id,
        content=content,
        author_id=user_id_str,
    )
    return ActivityRead.model_validate(activity)


@router.post("/calls", response_model=ActivityRead, status_code=status.HTTP_201_CREATED)
def create_call(
    lead_id: UUID,
    content: str,
    related_id: UUID | None = None,
    service: ActivitiesService = Depends(get_activities_service),
    current_user: tuple[str, str, str] = Depends(require_lead_manage),
) -> ActivityRead:
    """Create a call activity for a lead."""
    user_id_str, workspace_id_str, user_role = current_user
    activity = service.create_call(
        workspace_id=workspace_id_str,
        lead_id=lead_id,
        content=content,
        author_id=user_id_str,
        related_id=related_id,
    )
    return ActivityRead.model_validate(activity)


@router.get("/leads/{lead_id}/timeline")
def get_timeline(
    lead_id: UUID,
    type: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, le=100),
    service = Depends(get_activities_service),
    current_user: tuple[str, str, str] = Depends(require_lead_view),
) -> dict:
    """Get activity timeline for a lead."""
    user_id_str, workspace_id_str, user_role = current_user
    try:
        activities = service.get_timeline(
            lead_id=lead_id,
            workspace_id=workspace_id_str,
            user_id=user_id_str,
            activity_type=type,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"items": activities}


@router.post("/tasks", response_model=FollowUpTaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    task_data: FollowUpTaskCreate,
    service: ActivitiesService = Depends(get_activities_service),
    current_user: tuple[str, str, str] = Depends(require_lead_manage),
) -> FollowUpTaskRead:
    """Create a follow-up task."""
    user_id_str, workspace_id_str, user_role = current_user
    task = service.create_task(
        workspace_id=workspace_id_str,
        lead_id=task_data.lead_id,
        assignee_id=task_data.assignee_id or user_id_str,
        created_by_id=user_id_str,
        title=task_data.title,
        description=task_data.description,
        priority=task_data.priority,
        due_at=task_data.due_at,
        source_activity_id=task_data.source_activity_id,
    )
    return FollowUpTaskRead.model_validate(task)


@router.get("/tasks")
def list_tasks(
    status: str | None = Query(None),
    assignee_id: UUID | None = Query(None),
    due_before: datetime | None = Query(None),
    due_after: datetime | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, le=100),
    service = Depends(get_activities_service),
    current_user = Depends(require_lead_view),
):
    """List tasks with filters."""
    _, workspace_id_str, user_role = current_user
    tasks = service.list_tasks(
        workspace_id=workspace_id_str,
        assignee_id=assignee_id,
        status=status,
        due_before=due_before,
        due_after=due_after,
        cursor=cursor,
        limit=limit,
    )
    return {"items": [task.model_dump() for task in tasks]}


@router.get("/tasks/overdue")
def get_overdue_tasks(
    assignee_id: UUID | None = Query(None),
    service = Depends(get_activities_service),
    current_user = Depends(require_lead_view),
):
    """Get overdue tasks."""
    _, workspace_id_str, user_role = current_user
    tasks = service.get_overdue_tasks(
        workspace_id=workspace_id_str,
        assignee_id=assignee_id,
    )
    return {"items": [task.model_dump() for task in tasks]}


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: UUID,
    update_data: FollowUpTaskUpdate,
    service = Depends(get_activities_service),
    current_user = Depends(require_lead_manage),
):
    """Update a task."""
    _, workspace_id_str, _ = current_user
    task = service.update_task(
        task_id=task_id,
        workspace_id=workspace_id_str,
        title=update_data.title,
        description=update_data.description,
        status=update_data.status,
        priority=update_data.priority,
        due_at=update_data.due_at,
        assignee_id=update_data.assignee_id,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.model_dump()


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: UUID,
    service = Depends(get_activities_service),
    current_user = Depends(require_lead_manage),
):
    """Mark a task as completed."""
    _, workspace_id_str, _ = current_user
    task = service.complete_task(
        task_id=task_id,
        workspace_id=workspace_id_str,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.model_dump()
