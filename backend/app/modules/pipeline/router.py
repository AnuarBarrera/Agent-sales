from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.pipeline.schemas import (
    LeadStageChangeRequest,
    PipelineHistoryRead,
    PipelineStageCreate,
    PipelineStageRead,
    PipelineStageReorder,
    PipelineStageUpdate,
)
from app.modules.pipeline.service import PipelineService

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

def get_pipeline_service(db: Session = Depends(get_db)) -> PipelineService:
    return PipelineService(db)


# Permission dependencies
require_pipeline_admin = require_role_dependency([UserRole.ADMIN])
require_pipeline_view = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])


@router.get("/stages", response_model=list)
def list_stages(
    service: PipelineService = Depends(get_pipeline_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_pipeline_view),
) -> list[PipelineStageRead]:
    """List pipeline stages for current workspace."""
    _, workspace_id_str, _ = current_user
    stages = service.get_default_stages(workspace_id_str)
    return [PipelineStageRead.model_validate(s) for s in stages]


@router.post("/stages", response_model=PipelineStageRead, status_code=status.HTTP_201_CREATED)
def create_stage(
    stage_data: PipelineStageCreate,
    service: PipelineService = Depends(get_pipeline_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_pipeline_admin),
) -> PipelineStageRead:
    """Create a new pipeline stage (admin only)."""
    _, workspace_id_str, _ = current_user
    stage = service.create_stage(workspace_id_str, stage_data.key, stage_data.label, stage_data.order)
    return PipelineStageRead.model_validate(stage)


@router.patch("/stages/{stage_id}", response_model=PipelineStageRead)
def update_stage(
    stage_id: UUID,
    stage_data: PipelineStageUpdate,
    service: PipelineService = Depends(get_pipeline_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_pipeline_admin),
) -> PipelineStageRead:
    """Update a pipeline stage (admin only)."""
    _, workspace_id_str, _ = current_user
    stage = service.update_stage(stage_id, workspace_id_str, stage_data.label, stage_data.order, stage_data.is_active)
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")
    return PipelineStageRead.model_validate(stage)


@router.patch("/stages", response_model=list)
def reorder_stages(
    reorder_data: PipelineStageReorder,
    service: PipelineService = Depends(get_pipeline_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_pipeline_admin),
) -> list[PipelineStageRead]:
    """Reorder pipeline stages (admin only)."""
    _, workspace_id_str, _ = current_user
    stages = service.reorder_stages(workspace_id_str, reorder_data.stages)
    return [PipelineStageRead.model_validate(s) for s in stages]


@router.post("/leads/{lead_id}/stage", response_model=dict)
def change_lead_stage(
    lead_id: UUID,
    stage_change: LeadStageChangeRequest,
    service: PipelineService = Depends(get_pipeline_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])),
) -> dict:
    """Change a lead's pipeline stage."""
    user_id_str, workspace_id_str, user_role = current_user
    service.change_lead_stage(
        lead_id=lead_id,
        workspace_id=workspace_id_str,
        user_id=user_id_str,
        user_role=user_role,
        new_stage_id=stage_change.stage_id,
        reason=stage_change.reason,
    )
    return {"message": "Stage changed successfully", "lead_id": str(lead_id)}


@router.get("/leads/{lead_id}/history", response_model=list)
def get_lead_history(
    lead_id: UUID,
    service: PipelineService = Depends(get_pipeline_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])),
) -> list[PipelineHistoryRead]:
    """Get pipeline history for a lead."""
    _, workspace_id_str, _ = current_user
    history = service.get_lead_history(lead_id, workspace_id_str)
    return [PipelineHistoryRead.model_validate(h) for h in history]
