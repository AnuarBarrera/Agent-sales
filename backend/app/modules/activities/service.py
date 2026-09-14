import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import can_view_lead, require_member
from app.modules.activities.models import (
    Activity,
    ActivityType,
    FollowUpTask,
    TaskPriority,
    TaskStatus,
)
from app.modules.leads.models import Lead

logger = logging.getLogger(__name__)


class ActivitiesService:
    def __init__(self, db: Session):
        self.db = db

    def create_activity(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        activity_type: str,
        content: str,
        author_id: UUID,
        related_id: UUID | None = None,
    ) -> Activity:
        """Create a new activity."""
        lead = self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if not lead:
            raise ValueError("Lead not found")
        membership = require_member(self.db, author_id, workspace_id)
        if not can_view_lead(membership, lead):
            raise PermissionError("Not authorized to add activity to this lead")
        activity = Activity(
            workspace_id=workspace_id,
            lead_id=lead_id,
            type=activity_type,
            content=content,
            author_id=author_id,
            related_id=related_id,
        )
        self.db.add(activity)

        # Update lead's last_activity_at
        lead.last_activity_at = datetime.now(UTC)

        self.db.flush()
        return activity

    def create_assignment_activity(
        self,
        *,
        workspace_id: UUID,
        lead_id: UUID,
        author_id: UUID,
        previous_setter_id: UUID | None,
        new_setter_id: UUID | None,
        previous_closer_id: UUID | None,
        new_closer_id: UUID | None,
    ) -> Activity:
        """Record an assignment change using the current transaction."""
        content = json.dumps(
            {
                "event": "assignment",
                "setter_id_before": str(previous_setter_id) if previous_setter_id else None,
                "setter_id_after": str(new_setter_id) if new_setter_id else None,
                "closer_id_before": str(previous_closer_id) if previous_closer_id else None,
                "closer_id_after": str(new_closer_id) if new_closer_id else None,
            },
            sort_keys=True,
        )
        return self.create_activity(
            workspace_id=workspace_id,
            lead_id=lead_id,
            activity_type=ActivityType.ASSIGNMENT.value,
            content=content,
            author_id=author_id,
        )

    def create_note(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        content: str,
        author_id: UUID,
    ) -> Activity:
        """Create a note activity."""
        return self.create_activity(
            workspace_id=workspace_id,
            lead_id=lead_id,
            activity_type=ActivityType.NOTE.value,
            content=content,
            author_id=author_id,
        )

    def create_call(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        content: str,
        author_id: UUID,
        related_id: UUID | None = None,
    ) -> Activity:
        """Create a call activity."""
        return self.create_activity(
            workspace_id=workspace_id,
            lead_id=lead_id,
            activity_type=ActivityType.CALL.value,
            content=content,
            author_id=author_id,
            related_id=related_id,
        )

    def get_timeline(
        self,
        lead_id: UUID,
        workspace_id: UUID,
        user_id: UUID | None = None,
        activity_type: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[Activity]:
        """Get activity timeline for a lead."""
        lead = self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if not lead:
            raise ValueError("Lead not found")
        if user_id is not None:
            membership = require_member(self.db, user_id, workspace_id)
            if not can_view_lead(membership, lead):
                raise PermissionError("Not authorized to view this lead")

        query = select(Activity).where(
            Activity.lead_id == lead_id,
            Activity.workspace_id == workspace_id,
        )

        if activity_type:
            query = query.where(Activity.type == activity_type)

        if cursor:
            try:
                cursor_id = UUID(cursor)
                cursor_activity = self.db.get(Activity, cursor_id)
                if cursor_activity:
                    query = query.where(Activity.created_at < cursor_activity.created_at)
            except ValueError:
                pass

        query = query.order_by(Activity.created_at.desc()).limit(limit + 1)
        activities = list(self.db.execute(query).scalars().all())

        # For now, return all - pagination handled by caller
        return activities[:limit]

    def create_task(
        self,
        workspace_id: UUID,
        lead_id: UUID | None,
        assignee_id: UUID,
        created_by_id: UUID,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        due_at: datetime | None = None,
        source_activity_id: UUID | None = None,
    ) -> FollowUpTask:
        """Create a follow-up task."""
        task = FollowUpTask(
            workspace_id=workspace_id,
            lead_id=lead_id,
            assignee_id=assignee_id,
            created_by_id=created_by_id,
            title=title,
            description=description,
            priority=TaskPriority(priority),
            due_at=due_at,
            source_activity_id=source_activity_id,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def get_task(self, task_id: UUID, workspace_id: UUID) -> FollowUpTask | None:
        """Get a task by ID."""
        return self.db.execute(
            select(FollowUpTask).where(
                FollowUpTask.id == task_id,
                FollowUpTask.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def update_task(
        self,
        task_id: UUID,
        workspace_id: UUID,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        due_at: datetime | None = None,
        assignee_id: UUID | None = None,
    ) -> FollowUpTask | None:
        """Update a task."""
        task = self.get_task(task_id, workspace_id)
        if not task:
            return None

        if title is not None:
            task.title = title
        if description is not None:
            task.description = description
        if status is not None:
            task.status = TaskStatus(status)
            if status == "completed":
                task.completed_at = datetime.now(UTC)
        if priority is not None:
            task.priority = TaskPriority(priority)
        if due_at is not None:
            task.due_at = due_at
        if assignee_id is not None:
            task.assignee_id = assignee_id

        task.updated_at = datetime.now(UTC)
        self.db.flush()
        return task

    def list_tasks(
        self,
        workspace_id: UUID,
        assignee_id: UUID | None = None,
        status: str | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[FollowUpTask]:
        """List tasks with filters."""
        query = select(FollowUpTask).where(FollowUpTask.workspace_id == workspace_id)

        if assignee_id:
            query = query.where(FollowUpTask.assignee_id == assignee_id)

        if status:
            query = query.where(FollowUpTask.status == status)

        if due_before:
            query = query.where(FollowUpTask.due_at <= due_before)

        if due_after:
            query = query.where(FollowUpTask.due_at >= due_after)

        if cursor:
            try:
                cursor_id = UUID(cursor)
                cursor_task = self.db.get(FollowUpTask, cursor_id)
                if cursor_task:
                    query = query.where(FollowUpTask.created_at < cursor_task.created_at)
            except ValueError:
                pass

        query = query.order_by(FollowUpTask.due_at.asc().nulls_last(), FollowUpTask.created_at.desc()).limit(limit + 1)
        tasks = list(self.db.execute(query).scalars().all())

        # For now, return all - pagination handled by caller
        return tasks[:limit]

    def get_overdue_tasks(self, workspace_id: UUID, assignee_id: UUID | None = None) -> list[FollowUpTask]:
        """Get overdue tasks."""
        now = datetime.now(UTC)
        query = select(FollowUpTask).where(
            FollowUpTask.workspace_id == workspace_id,
            FollowUpTask.status != "completed",
            FollowUpTask.due_at < now,
        )

        if assignee_id:
            query = query.where(FollowUpTask.assignee_id == assignee_id)

        return list(self.db.execute(query).scalars().all())

    def complete_task(self, task_id: UUID, workspace_id: UUID) -> FollowUpTask | None:
        """Mark a task as completed."""
        task = self.get_task(task_id, workspace_id)
        if not task:
            return None

        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now(UTC)
        task.updated_at = datetime.now(UTC)
        self.db.flush()
        return task
