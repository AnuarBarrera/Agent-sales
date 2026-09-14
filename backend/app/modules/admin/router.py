from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.activities.models import FollowUpTask, TaskStatus
from app.modules.identity.models import Membership, MembershipStatus, User, UserRole
from app.modules.leads.models import Lead
from app.modules.pipeline.models import PipelineStage

router = APIRouter(prefix="/admin", tags=["admin"])
require_admin = require_role_dependency([UserRole.ADMIN])


class StageSummary(BaseModel):
    id: UUID
    key: str
    label: str
    lead_count: int


class MemberWorkload(BaseModel):
    id: UUID
    full_name: str
    role: UserRole
    lead_count: int


class AdminSummary(BaseModel):
    unassigned_leads: int
    overdue_tasks: int
    due_today: int
    stages: list[StageSummary]
    member_workload: list[MemberWorkload]


@router.get("/summary", response_model=AdminSummary)
def get_summary(
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_admin),
) -> AdminSummary:
    """Return workspace-scoped dashboard aggregates for administrators."""
    _, workspace_id, _ = current_user
    now = datetime.now(UTC)
    today_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    tomorrow = today_start + timedelta(days=1)

    unassigned_leads = db.scalar(
        select(func.count(Lead.id)).where(
            Lead.workspace_id == workspace_id,
            Lead.setter_id.is_(None),
            Lead.closer_id.is_(None),
        )
    ) or 0
    overdue_tasks = db.scalar(
        select(func.count(FollowUpTask.id)).where(
            FollowUpTask.workspace_id == workspace_id,
            FollowUpTask.status != TaskStatus.COMPLETED,
            FollowUpTask.due_at < now,
        )
    ) or 0
    due_today = db.scalar(
        select(func.count(FollowUpTask.id)).where(
            FollowUpTask.workspace_id == workspace_id,
            FollowUpTask.status != TaskStatus.COMPLETED,
            FollowUpTask.due_at >= today_start,
            FollowUpTask.due_at < tomorrow,
        )
    ) or 0

    stage_rows = db.execute(
        select(PipelineStage, func.count(Lead.id))
        .outerjoin(Lead, (Lead.pipeline_stage_id == PipelineStage.id) & (Lead.workspace_id == workspace_id))
        .where(PipelineStage.workspace_id == workspace_id)
        .group_by(PipelineStage.id)
        .order_by(PipelineStage.order)
    ).all()

    workload_rows = db.execute(
        select(User, Membership.role, func.count(func.distinct(Lead.id)))
        .join(Membership, Membership.user_id == User.id)
        .outerjoin(
            Lead,
            (Lead.workspace_id == workspace_id)
            & ((Lead.setter_id == User.id) | (Lead.closer_id == User.id)),
        )
        .where(Membership.workspace_id == workspace_id, Membership.status == MembershipStatus.ACTIVE)
        .group_by(User.id, Membership.role)
        .order_by(User.full_name)
    ).all()

    return AdminSummary(
        unassigned_leads=unassigned_leads,
        overdue_tasks=overdue_tasks,
        due_today=due_today,
        stages=[StageSummary(id=stage.id, key=stage.key, label=stage.label, lead_count=count) for stage, count in stage_rows],
        member_workload=[
            MemberWorkload(id=user.id, full_name=user.full_name, role=role, lead_count=count)
            for user, role, count in workload_rows
        ],
    )
