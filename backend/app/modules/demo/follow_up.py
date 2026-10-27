"""Tenant-scoped manual follow-up composed from the existing CRM services."""
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.modules.activities.models import Activity, FollowUpTask, TaskStatus
from app.modules.activities.schemas import ActivityRead, FollowUpTaskRead
from app.modules.activities.service import ActivitiesService
from app.modules.demo.errors import DemoError
from app.modules.demo.quota import DemoQuotaRepository
from app.modules.demo.schemas import DemoFollowUpRead
from app.modules.demo.service import require_demo_enabled
from app.modules.leads.models import Lead


class DemoFollowUpService:
    def __init__(self, db):
        self.db = db
        self.activities = ActivitiesService(db)
        self.repo = DemoQuotaRepository(db)

    def _lead(self, principal, lead_id, *, write=False):
        if write:
            require_demo_enabled()
            self.repo.lock_participant(principal)
        statement = select(Lead).where(Lead.id == lead_id, Lead.workspace_id == principal.workspace_id)
        if write:
            statement = statement.with_for_update(of=Lead).execution_options(populate_existing=True)
        lead = self.db.execute(statement).scalar_one_or_none()
        if lead is None:
            raise DemoError("demo_lead_not_found", "No se encontró el prospecto de esta demo.", 404)
        return lead

    def read(self, *, principal, lead_id):
        lead = self._lead(principal, lead_id)
        activities = self.db.execute(select(Activity).where(
            Activity.workspace_id == principal.workspace_id, Activity.lead_id == lead_id,
        ).order_by(Activity.created_at.desc(), Activity.id.desc()).limit(100)).scalars().all()
        # Active tasks are never hidden behind completed tasks in the history.
        tasks = self.db.execute(select(FollowUpTask).where(
            FollowUpTask.workspace_id == principal.workspace_id, FollowUpTask.lead_id == lead_id,
            FollowUpTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
        ).order_by(FollowUpTask.created_at.desc())).scalars().all()
        return DemoFollowUpRead(
            lead_id=lead_id, contacted=lead.contacted, first_contacted_at=lead.first_contacted_at,
            next_follow_up_at=lead.next_follow_up_at,
            activities=[ActivityRead.model_validate(row) for row in activities],
            tasks=[FollowUpTaskRead.model_validate(row) for row in tasks],
        )

    def _existing(self, principal, lead_id, request_id, kind, content):
        existing = self.db.get(Activity, request_id)
        if existing is None:
            return False
        if (existing.workspace_id != principal.workspace_id or existing.lead_id != lead_id
                or existing.author_id != principal.user_id or existing.type != kind or existing.content != content):
            raise DemoError("demo_follow_up_conflict", "La solicitud ya fue utilizada con otros datos.")
        return True

    def _event(self, principal, lead_id, request_id, kind, content, related_id=None):
        return self.activities.create_activity(
            workspace_id=principal.workspace_id, lead_id=lead_id, author_id=principal.user_id,
            activity_type=kind, content=content, related_id=related_id, activity_id=request_id,
        )

    def note(self, *, principal, lead_id, payload):
        self._lead(principal, lead_id, write=True)
        if not self._existing(principal, lead_id, payload.request_id, "note", payload.content):
            self._event(principal, lead_id, payload.request_id, "note", payload.content)
        return self.read(principal=principal, lead_id=lead_id)

    def contact(self, *, principal, lead_id, payload):
        lead = self._lead(principal, lead_id, write=True)
        content = json.dumps({"event": "manual_contact", "channel": payload.channel,
                              "outcome": payload.outcome, "note": payload.note}, sort_keys=True)
        kind = "call" if payload.channel == "phone" else "message"
        if not self._existing(principal, lead_id, payload.request_id, kind, content):
            self._event(principal, lead_id, payload.request_id, kind, content)
            lead.contacted = True
            lead.first_contacted_at = lead.first_contacted_at or datetime.now(UTC)
            lead.updated_at = datetime.now(UTC)
            self.db.flush()
        return self.read(principal=principal, lead_id=lead_id)

    def schedule(self, *, principal, lead_id, payload):
        lead = self._lead(principal, lead_id, write=True)
        content = json.dumps({"event": "task_scheduled", "title": payload.title,
                              "due_at": payload.due_at.astimezone(UTC).isoformat()}, sort_keys=True)
        if not self._existing(principal, lead_id, payload.request_id, "task", content):
            if payload.due_at <= datetime.now(UTC):
                raise DemoError("demo_follow_up_date_invalid", "Elige una fecha futura para el seguimiento.", 422)
            if self.db.get(FollowUpTask, payload.request_id) is not None:
                raise DemoError("demo_follow_up_conflict", "La solicitud ya fue utilizada con otros datos.")
            task = self.activities.create_task(workspace_id=principal.workspace_id, lead_id=lead_id,
                assignee_id=principal.user_id, created_by_id=principal.user_id,
                title=payload.title, due_at=payload.due_at, task_id=payload.request_id)
            self._event(principal, lead_id, payload.request_id, "task", content, task.id)
            self._sync_next(lead)
        return self.read(principal=principal, lead_id=lead_id)

    def complete(self, *, principal, lead_id, task_id, payload):
        lead = self._lead(principal, lead_id, write=True)
        task = self.activities.get_task(task_id, principal.workspace_id)
        if task is None or task.lead_id != lead_id:
            raise DemoError("demo_follow_up_task_not_found", "No se encontró el seguimiento de este prospecto.", 404)
        content = json.dumps({"event": "task_completed", "title": task.title, "task_id": str(task.id)}, sort_keys=True)
        repeated = self._existing(principal, lead_id, payload.request_id, "task", content)
        if task.status == TaskStatus.CANCELLED:
            raise DemoError("demo_follow_up_task_cancelled", "Este seguimiento fue cancelado.")
        if not repeated and task.status in {TaskStatus.PENDING, TaskStatus.IN_PROGRESS}:
            self.activities.complete_task(task.id, principal.workspace_id)
            self._event(principal, lead_id, payload.request_id, "task", content, task.id)
            self._sync_next(lead)
        return self.read(principal=principal, lead_id=lead_id)

    def _sync_next(self, lead):
        self.db.flush()
        active = self.db.execute(select(FollowUpTask).where(
            FollowUpTask.workspace_id == lead.workspace_id, FollowUpTask.lead_id == lead.id,
            FollowUpTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
        )).scalars().all()
        deadlines = [task.due_at for task in active if task.due_at is not None]
        lead.next_follow_up_at = min(deadlines) if deadlines else None
        lead.updated_at = datetime.now(UTC)
        self.db.flush()
