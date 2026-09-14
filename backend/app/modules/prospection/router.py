from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import can_view_run, require_role_dependency
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.identity.service import IdentityService
from app.modules.prospection.schemas import (
    ProspectionRunCreate,
    ProspectionRunEventResponse,
    ProspectionRunListItem,
    ProspectionRunRead,
    RunEventRead,
)
from app.modules.prospection.schemas import (
    ProspectionRunStatus as SchemaProspectionRunStatus,
)
from app.modules.prospection.service import ProspectionService

router = APIRouter(prefix="/prospection-runs", tags=["prospection"])


def serialize_run(run) -> ProspectionRunRead:
    """Serialize PostGIS values into the public JSON contract."""
    fields = {
        name: getattr(run, name)
        for name in ProspectionRunRead.model_fields
        if name != "polygon_geometry"
    }
    geometry = getattr(run, "polygon_geometry", None)
    fields["polygon_geometry"] = getattr(geometry, "desc", str(geometry)) if geometry else None
    return ProspectionRunRead.model_validate(fields)


def get_prospection_service(db: Session = Depends(get_db)) -> ProspectionService:
    return ProspectionService(db)


# Permission dependencies
require_prospection_create = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])
require_prospection_view = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])


def get_current_membership(
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_prospection_view),
) -> UUID:
    """Extract user_id from current_user tuple for membership checks."""
    user_id, _, _ = current_user
    return user_id


@router.post("", response_model=ProspectionRunRead, status_code=status.HTTP_202_ACCEPTED)
def create_prospection_run(
    run_data: ProspectionRunCreate,
    service: ProspectionService = Depends(get_prospection_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_prospection_create),
) -> ProspectionRunRead:
    """Create a new prospection run."""
    user_id, workspace_id, _ = current_user
    run = service.create_run(user_id, workspace_id, run_data)
    # Commit before enqueueing so a fast worker can see the run row.
    service.db.commit()
    try:
        from app.workers.queue import enqueue_prospection_run

        enqueue_prospection_run(str(run.id))
    except Exception as exc:
        service.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo encolar la prospección; verifica Redis",
        ) from exc
    return serialize_run(run)


@router.get("", response_model=list[ProspectionRunListItem])
def list_prospection_runs(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    service: ProspectionService = Depends(get_prospection_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_prospection_view),
) -> list[ProspectionRunListItem]:
    """List prospection runs for current workspace."""
    _, workspace_id, _ = current_user
    run_status = None
    if status_filter:
        from app.modules.prospection.models import ProspectionRunStatus
        try:
            run_status = ProspectionRunStatus(status_filter)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid status") from e

    runs = service.list_runs(workspace_id, run_status, limit, offset)
    return [ProspectionRunListItem.model_validate(r) for r in runs]


@router.get("/{run_id}", response_model=ProspectionRunRead)
def get_prospection_run(
    run_id: UUID,
    service: ProspectionService = Depends(get_prospection_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_prospection_view),
) -> ProspectionRunRead:
    """Get a prospection run by ID."""
    user_id, workspace_id, role = current_user
    run = service.get_run(run_id, workspace_id)
    if not run:
        raise HTTPException(status_code=404, detail="Prospection run not found")

    # Check permissions using the same request-scoped session.
    service_identity = IdentityService(service.db)
    membership = service_identity.get_active_membership(user_id, workspace_id)
    if not membership or not can_view_run(membership, run):
        raise HTTPException(status_code=403, detail="Not authorized to view this run")

    return serialize_run(run)


@router.get("/{run_id}/events", response_model=ProspectionRunEventResponse)
def get_prospection_run_events(
    run_id: UUID,
    service: ProspectionService = Depends(get_prospection_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_prospection_view),
) -> ProspectionRunEventResponse:
    """Get events for a prospection run (SSE alternative)."""
    user_id, workspace_id, role = current_user
    run = service.get_run(run_id, workspace_id)
    if not run:
        raise HTTPException(status_code=404, detail="Prospection run not found")

    # Check permissions using the same request-scoped session.
    service_identity = IdentityService(service.db)
    membership = service_identity.get_active_membership(user_id, workspace_id)
    if not membership or not can_view_run(membership, run):
        raise HTTPException(status_code=403, detail="Not authorized to view this run")

    events = service.get_run_events(run_id, workspace_id)

    # Convert model events to schema events
    event_schemas = [RunEventRead.model_validate(e) for e in events]

    # Convert model status to schema status
    schema_status = SchemaProspectionRunStatus(run.status.value)

    return ProspectionRunEventResponse(
        run_id=run.id,
        status=schema_status,
        found_count=run.found_count,
        new_count=run.new_count,
        duplicate_count=run.duplicate_count,
        error_count=run.error_count,
        error_summary=run.error_summary,
        events=event_schemas,
    )


@router.post("/{run_id}/validate", status_code=status.HTTP_202_ACCEPTED)
def trigger_validation(
    run_id: UUID,
    service: ProspectionService = Depends(get_prospection_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_prospection_view),
) -> dict[str, str]:
    """Trigger validation for all leads in a prospection run."""
    user_id, workspace_id, role = current_user
    run = service.get_run(run_id, workspace_id)
    if not run:
        raise HTTPException(status_code=404, detail="Prospection run not found")

    service_identity = IdentityService(service.db)
    membership = service_identity.get_active_membership(user_id, workspace_id)
    if not membership or not can_view_run(membership, run):
        raise HTTPException(status_code=403, detail="Not authorized to validate this run")

    try:
        from app.workers.queue import enqueue_validation_run

        job_id = enqueue_validation_run(str(run_id))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo encolar la validación; verifica Redis",
        ) from exc
    return {"message": "Validation triggered", "run_id": str(run_id), "job_id": job_id}
