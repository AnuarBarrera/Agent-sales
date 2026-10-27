import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.permissions import can_manage_lead
from app.modules.identity.models import Membership, MembershipStatus, UserRole
from app.modules.leads.models import Business, Lead
from app.modules.leads.schemas import (
    BulkAssignResponse,
    LeadFilters,
    LeadListItem,
    LeadListResponse,
    LeadProfile,
    ValidationStatus,
)

logger = logging.getLogger(__name__)


class LeadService:
    def __init__(self, db: Session):
        self.db = db

    def get_lead(self, lead_id: UUID, workspace_id: UUID) -> Lead | None:
        """Get a lead by ID."""
        return self.db.execute(
            select(Lead)
            .options(joinedload(Lead.business))
            .where(
                Lead.id == lead_id,
                Lead.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list_leads(
        self,
        workspace_id: UUID,
        filters: LeadFilters,
        user_id: UUID,
        user_role: str,
    ) -> LeadListResponse:
        """List leads with filters and pagination."""
        role_value = user_role.value if isinstance(user_role, UserRole) else user_role
        query = select(Lead).options(joinedload(Lead.business)).where(Lead.workspace_id == workspace_id)

        # Apply permission-based filtering
        if role_value == UserRole.SETTER.value:
            from app.modules.prospection.models import ProspectionRun
            query = query.outerjoin(ProspectionRun, ProspectionRun.id == Lead.source_run_id).where(
                (Lead.setter_id == user_id) | (ProspectionRun.created_by_id == user_id)
            )
        elif role_value == UserRole.CLOSER.value:
            query = query.where(Lead.closer_id == user_id)
        # Admin sees all

        # Apply filters
        if filters.q:
            query = query.join(Business).where(
                (Business.name.ilike(f"%{filters.q}%")) |
                (Business.address.ilike(f"%{filters.q}%")) |
                (Business.phone.ilike(f"%{filters.q}%"))
            )

        if filters.profile:
            query = query.where(Lead.profile == filters.profile)

        if filters.validation_status:
            query = query.where(Lead.validation_status == filters.validation_status)

        if filters.setter_id:
            query = query.where(Lead.setter_id == filters.setter_id)

        if filters.closer_id:
            query = query.where(Lead.closer_id == filters.closer_id)

        if filters.run_id:
            query = query.where(Lead.source_run_id == filters.run_id)

        if filters.pipeline_stage:
            query = query.where(Lead.pipeline_stage_id == filters.pipeline_stage)

        if filters.has_website is not None:
            if filters.has_website:
                query = query.join(Business).where(Business.website.isnot(None))
            else:
                query = query.join(Business).where(Business.website.is_(None))

        # Apply cursor-based pagination
        if filters.cursor:
            try:
                cursor_id = UUID(filters.cursor)
                cursor_lead = self.db.get(Lead, cursor_id)
                if cursor_lead:
                    query = query.where(Lead.created_at < cursor_lead.created_at)
            except ValueError:
                pass

        query = query.order_by(Lead.created_at.desc()).limit(filters.limit + 1)
        leads = list(self.db.execute(query).scalars().all())

        has_more = len(leads) > filters.limit
        if has_more:
            leads = leads[:-1]
            next_cursor = str(leads[-1].id) if leads else None
        else:
            next_cursor = None

        items = []
        for lead in leads:
            item = LeadListItem(
                id=lead.id,
                name=lead.business.name if lead.business else "Unknown",
                address=lead.business.address if lead.business else None,
                phone=lead.business.phone if lead.business else None,
                website=lead.business.website if lead.business else None,
                profile=LeadProfile(lead.profile.value) if lead.profile else LeadProfile.UNCLASSIFIED,
                validation_status=ValidationStatus(lead.validation_status.value) if lead.validation_status else ValidationStatus.PENDING,
                pipeline_stage_name=lead.pipeline_stage_name,
                setter_name=lead.setter_name,
                closer_name=lead.closer_name,
                priority=lead.priority,
                next_follow_up_at=lead.next_follow_up_at,
                contacted=lead.contacted,
                last_activity_at=lead.last_activity_at,
                created_at=lead.created_at,
            )
            items.append(item)

        return LeadListResponse(items=items, next_cursor=next_cursor)

    def update_lead(
        self,
        lead_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        user_role: str,
        setter_id: UUID | None = None,
        closer_id: UUID | None = None,
        profile: LeadProfile | None = None,
        validation_status: ValidationStatus | None = None,
        pipeline_stage_id: UUID | None = None,
        score: int | None = None,
        score_reason: str | None = None,
        priority: int | None = None,
        estimated_value: float | None = None,
        lost_reason: str | None = None,
        next_follow_up_at: datetime | None = None,
    ) -> Lead | None:
        """Update a lead."""
        from app.modules.identity.models import Membership, MembershipStatus
        lead = self.get_lead(lead_id, workspace_id)
        if not lead:
            return None

        # Check permissions
        membership = self.db.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        ).scalar_one_or_none()

        if not membership or not can_manage_lead(membership, lead):
            raise PermissionError("Not authorized to update this lead")

        previous_setter_id = lead.setter_id
        previous_closer_id = lead.closer_id
        if setter_id is not None:
            lead.setter_id = setter_id
        if closer_id is not None:
            lead.closer_id = closer_id
        if profile is not None:
            lead.profile = profile  # type: ignore[assignment]
        if validation_status is not None:
            lead.validation_status = validation_status  # type: ignore[assignment]
        if pipeline_stage_id is not None:
            lead.pipeline_stage_id = pipeline_stage_id
        if score is not None:
            lead.score = score
        if score_reason is not None:
            lead.score_reason = score_reason
        if priority is not None:
            lead.priority = priority
        if estimated_value is not None:
            lead.estimated_value = estimated_value
        if lost_reason is not None:
            lead.lost_reason = lost_reason
        if next_follow_up_at is not None:
            lead.next_follow_up_at = next_follow_up_at

        lead.updated_at = datetime.now(UTC)
        if lead.setter_id != previous_setter_id or lead.closer_id != previous_closer_id:
            from app.modules.activities.service import ActivitiesService

            ActivitiesService(self.db).create_assignment_activity(
                workspace_id=workspace_id,
                lead_id=lead.id,
                author_id=user_id,
                previous_setter_id=previous_setter_id,
                new_setter_id=lead.setter_id,
                previous_closer_id=previous_closer_id,
                new_closer_id=lead.closer_id,
            )
        self.db.flush()
        return lead

    def bulk_assign(
        self,
        workspace_id: UUID,
        user_id: UUID,
        user_role: str,
        lead_ids: list[UUID],
        setter_id: UUID | None = None,
        closer_id: UUID | None = None,
    ) -> BulkAssignResponse:
        """Bulk assign leads to setter/closer."""
        role_value = user_role.value if isinstance(user_role, UserRole) else user_role

        # Check permissions
        if role_value == UserRole.SETTER.value:
            raise PermissionError("Setters cannot assign leads")
        if role_value == UserRole.CLOSER.value:
            # Closers can only assign to themselves as closer
            if setter_id is not None:
                raise PermissionError("Closers cannot assign setters")
            if closer_id is not None and closer_id != user_id:
                raise PermissionError("Closers can only assign to themselves")

        assigned = []
        failed = {}

        for lead_id in lead_ids:
            lead = self.get_lead(lead_id, workspace_id)
            if not lead:
                failed[lead_id] = "Lead not found"
                continue

            # Check if user can view/manage this lead
            membership = self.db.execute(
                select(Membership).where(
                    Membership.user_id == user_id,
                    Membership.workspace_id == workspace_id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            ).scalar_one_or_none()

            if not membership or not can_manage_lead(membership, lead):
                failed[lead_id] = "Not authorized to assign this lead"
                continue

            previous_setter_id = lead.setter_id
            previous_closer_id = lead.closer_id
            if setter_id is not None:
                lead.setter_id = setter_id
            if closer_id is not None:
                lead.closer_id = closer_id

            lead.updated_at = datetime.now(UTC)
            assigned.append(lead_id)

            if lead.setter_id != previous_setter_id or lead.closer_id != previous_closer_id:
                from app.modules.activities.service import ActivitiesService

                ActivitiesService(self.db).create_assignment_activity(
                    workspace_id=workspace_id,
                    lead_id=lead.id,
                    author_id=user_id,
                    previous_setter_id=previous_setter_id,
                    new_setter_id=lead.setter_id,
                    previous_closer_id=previous_closer_id,
                    new_closer_id=lead.closer_id,
                )

        self.db.flush()
        return BulkAssignResponse(assigned=assigned, failed=failed)
