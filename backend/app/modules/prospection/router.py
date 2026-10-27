from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import can_view_run, require_role_dependency
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.identity.service import IdentityService
from app.modules.prospection.schemas import (
    ProspectionRunCreate,
    ProspectionRunEventResponse,
    ProspectionRunFitResults,
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
    if run_data.discovery_strategy is not None:
        from app.modules.lead_fit.router import require_lead_fit_enabled

        try:
            require_lead_fit_enabled()
        except HTTPException:
            raise
    try:
        run = service.create_run(user_id, workspace_id, run_data)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Approved policy changed; refresh the search plan") from exc
    response_snapshot = serialize_run(run)
    run_id_text = str(run.id)
    # Commit before enqueueing so a fast worker can see the run row.
    service.db.commit()
    try:
        from app.workers.queue import enqueue_prospection_run

        enqueue_prospection_run(run_id_text)
    except Exception as exc:
        service.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo encolar la prospección; verifica Redis",
        ) from exc
    return response_snapshot


@router.get("/{run_id}/fit-results", response_model=ProspectionRunFitResults)
def get_fit_results(
    run_id: UUID,
    classification: str | None = Query(None),
    evaluation_status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: ProspectionService = Depends(get_prospection_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_prospection_view),
) -> ProspectionRunFitResults:
    """Return latest per-lead fit rows plus discovery and decision counts."""
    from app.modules.lead_fit.models import LeadFitEvaluation, LeadFitEvaluationStatus
    from app.modules.lead_fit.router import require_lead_fit_enabled
    from app.modules.lead_fit.schemas import FitEvaluationRead
    from app.modules.prospection.models import RunEvent, RunLead

    require_lead_fit_enabled()
    user_id, workspace_id, _ = current_user
    run = service.get_run(run_id, workspace_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Prospection run not found")
    member = IdentityService(service.db).get_active_membership(user_id, workspace_id)
    if member is None or not can_view_run(member, run):
        raise HTTPException(status_code=403, detail="Not authorized to view this run")
    if run.fit_policy_id is None:
        raise HTTPException(status_code=409, detail="Run has no Lead Fit policy snapshot")
    if classification not in {None, "qualified", "needs_more_research", "not_qualified"}:
        raise HTTPException(status_code=400, detail="Invalid classification filter")
    if evaluation_status not in {None, *(item.value for item in LeadFitEvaluationStatus)}:
        raise HTTPException(status_code=400, detail="Invalid evaluation status filter")

    lead_ids = select(RunLead.lead_id).where(
        RunLead.workspace_id == workspace_id,
        RunLead.run_id == run_id,
        RunLead.lead_id.is_not(None),
    )
    latest = select(
        LeadFitEvaluation.id.label("evaluation_id"),
        func.row_number().over(
            partition_by=LeadFitEvaluation.lead_id,
            order_by=LeadFitEvaluation.requested_at.desc(),
        ).label("row_number"),
    ).where(
        LeadFitEvaluation.workspace_id == workspace_id,
        LeadFitEvaluation.policy_id == run.fit_policy_id,
        LeadFitEvaluation.lead_id.in_(lead_ids),
    ).subquery()
    base = select(LeadFitEvaluation).join(
        latest, latest.c.evaluation_id == LeadFitEvaluation.id,
    ).where(latest.c.row_number == 1)
    filtered = base
    if classification is not None:
        filtered = filtered.where(LeadFitEvaluation.classification == classification)
    if evaluation_status is not None:
        filtered = filtered.where(LeadFitEvaluation.status == evaluation_status)
    total = service.db.execute(select(func.count()).select_from(filtered.subquery())).scalar_one()
    rows = service.db.execute(
        filtered.order_by(LeadFitEvaluation.requested_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    counts_statement = (
        select(LeadFitEvaluation.status, LeadFitEvaluation.classification, func.count())
        .join(latest, latest.c.evaluation_id == LeadFitEvaluation.id)
        .where(
            latest.c.row_number == 1,
            LeadFitEvaluation.workspace_id == workspace_id,
        )
    )
    if classification is not None:
        counts_statement = counts_statement.where(LeadFitEvaluation.classification == classification)
    if evaluation_status is not None:
        counts_statement = counts_statement.where(LeadFitEvaluation.status == evaluation_status)
    counts_rows = service.db.execute(
        counts_statement.group_by(LeadFitEvaluation.status, LeadFitEvaluation.classification)
    ).all()
    evaluation_lead_ids = select(LeadFitEvaluation.lead_id).where(
        LeadFitEvaluation.workspace_id == workspace_id,
        LeadFitEvaluation.policy_id == run.fit_policy_id,
        LeadFitEvaluation.lead_id.in_(lead_ids),
    )
    missing_evaluation_leads = service.db.execute(select(RunLead.lead_id).where(
        RunLead.workspace_id == workspace_id,
        RunLead.run_id == run_id,
        RunLead.lead_id.is_not(None),
        RunLead.lead_id.not_in(evaluation_lead_ids),
    ).distinct()).scalars().all()
    unavailable_events = service.db.execute(
        select(RunEvent).where(
            RunEvent.run_id == run_id,
            RunEvent.event_type == "prospection.fit_evaluation.unavailable",
        )
    ).scalars().all()
    unavailable_lead_ids = {str(lead_id) for lead_id in missing_evaluation_leads}
    unavailable_lead_ids.update(
        event.payload["lead_id"]
        for event in unavailable_events
        if isinstance(event.payload, dict) and isinstance(event.payload.get("lead_id"), str)
    )
    counts = {"qualified": 0, "needs_more_research": 0, "not_qualified": 0,
              "evaluation_failed": 0, "pending": 0}
    for row in counts_rows:
        row_status, row_classification, amount = row
        if row_classification in counts:
            counts[row_classification] += amount
        if row_status == LeadFitEvaluationStatus.FAILED:
            counts["evaluation_failed"] += amount
        if row_status in {LeadFitEvaluationStatus.PENDING, LeadFitEvaluationStatus.RUNNING}:
            counts["pending"] += amount
    return ProspectionRunFitResults(
        run_id=run.id,
        found_count=run.found_count,
        new_count=run.new_count,
        duplicate_count=run.duplicate_count,
        discovery_failed_count=run.error_count,
        evaluation_unavailable_count=len(unavailable_lead_ids),
        total=total,
        **counts,
        items=[FitEvaluationRead.model_validate(row).model_dump(mode="json") for row in rows],
    )


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
