import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import can_change_stage, can_view_lead
from app.modules.identity.models import Membership, MembershipStatus, UserRole
from app.modules.leads.models import Lead
from app.modules.pipeline.models import PipelineHistory, PipelineStage

logger = logging.getLogger(__name__)


class PipelineService:
    def __init__(self, db: Session):
        self.db = db

    def get_stage(self, stage_id: UUID, workspace_id: UUID) -> PipelineStage | None:
        """Get a pipeline stage by ID."""
        return self.db.execute(
            select(PipelineStage).where(
                PipelineStage.id == stage_id,
                PipelineStage.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list_stages(self, workspace_id: UUID) -> list[PipelineStage]:
        """List pipeline stages for a workspace."""
        return list(self.db.execute(
            select(PipelineStage)
            .where(PipelineStage.workspace_id == workspace_id)
            .order_by(PipelineStage.order)
        ).scalars().all())

    def create_stage(
        self,
        workspace_id: UUID,
        key: str,
        label: str,
        order: int,
    ) -> PipelineStage:
        """Create a new pipeline stage."""
        # Check if key already exists
        existing = self.db.execute(
            select(PipelineStage).where(
                PipelineStage.workspace_id == workspace_id,
                PipelineStage.key == key,
            )
        ).scalar_one_or_none()
        if existing:
            raise ValueError(f"Stage with key '{key}' already exists")

        stage = PipelineStage(
            workspace_id=workspace_id,
            key=key,
            label=label,
            order=order,
        )
        self.db.add(stage)
        self.db.flush()
        return stage

    def update_stage(
        self,
        stage_id: UUID,
        workspace_id: UUID,
        label: str | None = None,
        order: int | None = None,
        is_active: bool | None = None,
    ) -> PipelineStage | None:
        """Update a pipeline stage."""
        stage = self.get_stage(stage_id, workspace_id)
        if not stage:
            return None

        if label is not None:
            stage.label = label
        if order is not None:
            stage.order = order
        if is_active is not None:
            stage.is_active = is_active

        stage.updated_at = datetime.now(UTC)
        self.db.flush()
        return stage

    def reorder_stages(self, workspace_id: UUID, stages: list[dict]) -> list[PipelineStage]:
        """Reorder pipeline stages."""
        for idx, stage_data in enumerate(stages):
            stage = self.get_stage(stage_data["id"], workspace_id)
            if stage:
                stage.order = stage_data.get("order", idx)
                if "label" in stage_data:
                    stage.label = stage_data["label"]
                stage.updated_at = datetime.now(UTC)
        self.db.flush()
        return self.list_stages(workspace_id)

    def change_lead_stage(
        self,
        lead_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        user_role: str,
        new_stage_id: UUID,
        reason: str | None = None,
    ) -> Lead:
        """Change a lead's pipeline stage."""

        lead = self.db.get(Lead, lead_id)
        if not lead or lead.workspace_id != workspace_id:
            raise ValueError("Lead not found")

        # Verify the destination belongs to the current workspace.
        new_stage = self.get_stage(new_stage_id, workspace_id)
        if not new_stage:
            raise ValueError("Invalid stage")

        # Check permissions
        membership = self.db.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        ).scalar_one_or_none()

        if not membership:
            raise PermissionError("User not a member of this workspace")

        if membership.role != UserRole.ADMIN and not can_view_lead(membership, lead):
            raise PermissionError("Not authorized to change this lead's stage")

        # Check if user can change stage
        if not can_change_stage(membership, lead, new_stage.key):
            raise PermissionError("Not authorized to change this lead's stage")

        # Record history
        from_stage_id = lead.pipeline_stage_id
        lead.pipeline_stage_id = new_stage_id
        lead.updated_at = datetime.now(UTC)

        history = PipelineHistory(
            lead_id=lead_id,
            from_stage_id=from_stage_id,
            to_stage_id=new_stage_id,
            changed_by_id=user_id,
            reason=reason,
        )
        self.db.add(history)
        self.db.flush()
        return lead

    def get_lead_history(self, lead_id: UUID, workspace_id: UUID) -> list[PipelineHistory]:
        """Get pipeline history for a lead."""
        lead = self.db.get(Lead, lead_id)
        if not lead or lead.workspace_id != workspace_id:
            return []

        return list(self.db.execute(
            select(PipelineHistory)
            .where(PipelineHistory.lead_id == lead_id)
            .order_by(PipelineHistory.created_at.desc())
        ).scalars().all())

    def get_default_stages(self, workspace_id: UUID) -> list[PipelineStage]:
        """Get default pipeline stages for a workspace, creating them if needed."""
        stages = self.list_stages(workspace_id)
        if not stages:
            # Create default stages
            default_stages = [
                {"key": "new", "label": "Nuevo", "order": 0},
                {"key": "validated", "label": "Validado", "order": 10},
                {"key": "to_contact", "label": "A Contactar", "order": 20},
                {"key": "contacted", "label": "Contactado", "order": 30},
                {"key": "conversation", "label": "En Conversación", "order": 40},
                {"key": "appointment", "label": "Cita Agendada", "order": 50},
                {"key": "proposal", "label": "Propuesta Enviada", "order": 60},
                {"key": "won", "label": "Ganado", "order": 70},
                {"key": "lost", "label": "Perdido", "order": 80},
                {"key": "not_interested", "label": "No Interesado", "order": 90},
            ]
            created = []
            for stage_data in default_stages:
                stage = PipelineStage(
                    workspace_id=workspace_id,
                    key=stage_data["key"],
                    label=stage_data["label"],
                    order=stage_data["order"],
                )
                self.db.add(stage)
                created.append(stage)
            self.db.flush()
            return list(self.db.execute(
                select(PipelineStage).where(PipelineStage.workspace_id == workspace_id).order_by(PipelineStage.order)
            ).scalars().all())
        return stages
