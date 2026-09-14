from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.activities.schemas import ActivityCreate, ActivityRead
from app.modules.activities.service import ActivitiesService
from app.modules.identity.models import UserRole
from app.modules.leads.importer import LeadImportError, import_lead_rows, parse_leads_csv
from app.modules.leads.schemas import (
    BulkAssignRequest,
    BulkAssignResponse,
    LeadFilters,
    LeadImportResponse,
    LeadListResponse,
    LeadProfile,
    LeadRead,
    LeadUpdate,
    ValidationStatus,
)
from app.modules.leads.service import LeadService
from app.modules.pipeline.schemas import LeadStageChangeRequest
from app.modules.pipeline.service import PipelineService

router = APIRouter(prefix="/leads", tags=["leads"])

def get_lead_service(db: Session = Depends(get_db)) -> LeadService:
    return LeadService(db)


def get_activities_service(db: Session = Depends(get_db)) -> ActivitiesService:
    return ActivitiesService(db)


def get_pipeline_service(db: Session = Depends(get_db)) -> PipelineService:
    return PipelineService(db)


# Permission dependencies
require_lead_view = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])
require_lead_manage = require_role_dependency([UserRole.ADMIN, UserRole.CLOSER])
require_lead_import = require_role_dependency([UserRole.ADMIN, UserRole.SETTER])


@router.post("/import", response_model=LeadImportResponse, status_code=status.HTTP_201_CREATED)
def import_leads_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_import),
) -> LeadImportResponse:
    """Import a Google Sheets/n8n CSV export into the current workspace."""
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="El archivo debe tener extensión .csv")
    try:
        rows = parse_leads_csv(file.file)
        result = import_lead_rows(db, current_user[1], rows)
    except LeadImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LeadImportResponse(
        created=result.created,
        updated=result.updated,
        skipped=result.skipped,
        errors=result.errors,
    )


@router.get("", response_model=LeadListResponse)
def list_leads(
    q: str | None = Query(None),
    profile: str | None = Query(None),
    validation_status: str | None = Query(None),
    setter_id: str | None = Query(None),
    closer_id: str | None = Query(None),
    run_id: str | None = Query(None),
    pipeline_stage: str | None = Query(None),
    has_website: bool | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, le=100),
    service: LeadService = Depends(get_lead_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_view),
) -> LeadListResponse:
    """List leads with filters and cursor pagination."""
    user_id_str, workspace_id_str, user_role = current_user
    workspace_id = workspace_id_str

    filters = LeadFilters(
        q=q,
        profile=LeadProfile(profile) if profile else None,
        validation_status=ValidationStatus(validation_status) if validation_status else None,
        setter_id=UUID(setter_id) if setter_id else None,
        closer_id=UUID(closer_id) if closer_id else None,
        run_id=UUID(run_id) if run_id else None,
        pipeline_stage=UUID(pipeline_stage) if pipeline_stage else None,
        has_website=has_website,
        cursor=cursor,
        limit=limit,
    )

    result = service.list_leads(workspace_id, filters, user_id_str, user_role)
    return result


@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(
    lead_id: UUID,
    service: LeadService = Depends(get_lead_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_view),
) -> LeadRead:
    """Get a lead by ID."""
    user_id_str, workspace_id_str, _ = current_user
    workspace_id = workspace_id_str

    lead = service.get_lead(lead_id, workspace_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Check permissions
    from app.core.permissions import can_view_lead, require_member
    from app.modules.identity.models import UserRole
    from app.modules.prospection.models import ProspectionRun

    membership = require_member(service.db, user_id_str, workspace_id)

    created_run = service.db.get(ProspectionRun, lead.source_run_id) if lead.source_run_id else None
    can_view_created_run = (
        membership
        and membership.role == UserRole.SETTER
        and created_run is not None
        and created_run.created_by_id == membership.user_id
    )
    if not membership or (not can_view_lead(membership, lead) and not can_view_created_run):
        raise HTTPException(status_code=403, detail="Not authorized to view this lead")

    return LeadRead.model_validate(lead)


@router.post("/{lead_id}/activities", response_model=ActivityRead, status_code=status.HTTP_201_CREATED)
def create_lead_activity(
    lead_id: UUID,
    activity_data: ActivityCreate,
    service: ActivitiesService = Depends(get_activities_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_view),
) -> ActivityRead:
    """Append an activity using the resource-oriented CRM route."""
    user_id, workspace_id, _ = current_user
    if activity_data.lead_id != lead_id:
        raise HTTPException(status_code=400, detail="lead_id does not match the URL")
    try:
        activity = service.create_activity(
            workspace_id=workspace_id,
            lead_id=lead_id,
            activity_type=activity_data.type,
            content=activity_data.content,
            author_id=user_id,
            related_id=activity_data.related_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ActivityRead.model_validate(activity)


@router.get("/{lead_id}/timeline")
def get_lead_timeline(
    lead_id: UUID,
    limit: int = Query(50, le=100),
    service: ActivitiesService = Depends(get_activities_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_view),
) -> dict[str, list[ActivityRead]]:
    """Return the lead timeline using the resource-oriented CRM route."""
    user_id, workspace_id, _ = current_user
    try:
        activities = service.get_timeline(lead_id, workspace_id, user_id=user_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"items": [ActivityRead.model_validate(activity) for activity in activities]}


@router.post("/{lead_id}/stage", response_model=dict)
def change_lead_stage_resource(
    lead_id: UUID,
    stage_change: LeadStageChangeRequest,
    service: PipelineService = Depends(get_pipeline_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_view),
) -> dict[str, str]:
    """Change a lead stage using the resource-oriented CRM route."""
    user_id, workspace_id, user_role = current_user
    try:
        service.change_lead_stage(
            lead_id=lead_id,
            workspace_id=workspace_id,
            user_id=user_id,
            user_role=user_role,
            new_stage_id=stage_change.stage_id,
            reason=stage_change.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"message": "Stage changed successfully", "lead_id": str(lead_id)}


@router.patch("/{lead_id}", response_model=LeadRead)
def update_lead(
    lead_id: UUID,
    update_data: LeadUpdate,
    service: LeadService = Depends(get_lead_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_manage),
) -> LeadRead:
    """Update a lead."""
    user_id_str, workspace_id_str, user_role = current_user
    workspace_id = workspace_id_str

    lead = service.update_lead(
        lead_id=lead_id,
        workspace_id=workspace_id,
        user_id=user_id_str,
        user_role=user_role,
        setter_id=update_data.setter_id,
        closer_id=update_data.closer_id,
        profile=update_data.profile,
        validation_status=update_data.validation_status,
        pipeline_stage_id=update_data.pipeline_stage_id,
        score=update_data.score,
        score_reason=update_data.score_reason,
        priority=update_data.priority,
        estimated_value=update_data.estimated_value,
        lost_reason=update_data.lost_reason,
        next_follow_up_at=update_data.next_follow_up_at,
    )

    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    return LeadRead.model_validate(lead)


@router.post("/bulk-assign", response_model=BulkAssignResponse, status_code=status.HTTP_201_CREATED)
def bulk_assign(
    request: BulkAssignRequest,
    service: LeadService = Depends(get_lead_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_lead_manage),
) -> BulkAssignResponse:
    """Bulk assign leads to setter/closer."""
    user_id_str, workspace_id_str, user_role = current_user
    workspace_id = workspace_id_str

    result = service.bulk_assign(
        workspace_id=workspace_id,
        user_id=user_id_str,
        user_role=user_role,
        lead_ids=request.lead_ids,
        setter_id=request.setter_id,
        closer_id=request.closer_id,
    )

    return result
