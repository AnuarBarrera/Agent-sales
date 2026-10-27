from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.permissions import require_member, require_role_dependency
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.identity.router import get_current_user
from app.modules.sales_playbook.commercial_context import (
    commercial_readiness,
    commercial_review_digest,
)
from app.modules.sales_playbook.commercial_service import (
    CommercialContextDraftCreate,
    CommercialContextDraftUpdate,
    CommercialContextService,
)
from app.modules.sales_playbook.commercial_service import (
    IdempotencyConflict as CommercialIdempotencyConflict,
)
from app.modules.sales_playbook.factory import get_sales_playbook_generator
from app.modules.sales_playbook.generation_schemas import SalesPlaybookGenerationJobRead
from app.modules.sales_playbook.generation_service import (
    GenerationJobIdempotencyConflict,
    GenerationResumeRejected,
    SalesPlaybookGenerationJobService,
)
from app.modules.sales_playbook.schemas import (
    SalesPlaybookApprove,
    SalesPlaybookDraftCreate,
    SalesPlaybookRead,
    SalesPlaybookReject,
    SalesPlaybookWorkflowConflictResolve,
    SalesPlaybookWorkflowTransitionComplete,
)
from app.modules.sales_playbook.service import (
    IdempotencyConflict,
    SalesPlaybookService,
    approval_readiness,
    content_digest,
)

router = APIRouter(prefix="/sales-playbooks", tags=["sales-playbooks"])
require_playbook_approver = require_role_dependency([UserRole.ADMIN, UserRole.CLOSER])


def get_service(db: Session = Depends(get_db)) -> SalesPlaybookService:
    return SalesPlaybookService(db, generator=get_sales_playbook_generator())


def get_read_service(db: Session = Depends(get_db)) -> SalesPlaybookService:
    """Construct a query-only service without initializing a generation provider."""
    return SalesPlaybookService(db, generator=None)


def get_commercial_service(db: Session = Depends(get_db)) -> CommercialContextService:
    return CommercialContextService(db)


def get_generation_job_service(db: Session = Depends(get_db)) -> SalesPlaybookGenerationJobService:
    return SalesPlaybookGenerationJobService(db)


def _read(playbook) -> SalesPlaybookRead:
    result = SalesPlaybookRead.model_validate(playbook)
    if playbook.content_contract_version == "3.0":
        return result.model_copy(update={
            "approval_readiness": approval_readiness(playbook.structured_content),
            "content_digest": content_digest(playbook.structured_content),
        })
    if playbook.content_contract_version == "4.0":
        from app.modules.sales_playbook.commercial_context import (
            CommercialContextContent,
            CommercialSourceSnapshot,
        )
        content = CommercialContextContent.model_validate(playbook.structured_content)
        audit = playbook.generation_audit or {}
        cc_audit = audit.get("commercial_context", {})
        source_snapshot_data = cc_audit.get("source_snapshot")
        if source_snapshot_data:
            source_snapshot = CommercialSourceSnapshot.model_validate(source_snapshot_data)
            return result.model_copy(update={
                "commercial_readiness": commercial_readiness(content),
                "content_digest": commercial_review_digest(playbook.workspace_id, content, source_snapshot),
            })
    return result


@router.get("", response_model=list[SalesPlaybookRead])
def list_playbooks(
    current_user=Depends(get_current_user),
    service: SalesPlaybookService = Depends(get_read_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    return [_read(item) for item in service.list(workspace_id)]


@router.post("/drafts", response_model=SalesPlaybookGenerationJobRead,
             status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
@router.post("/generation-jobs", response_model=SalesPlaybookGenerationJobRead,
             status_code=status.HTTP_202_ACCEPTED)
def create_generation_job(
    data: SalesPlaybookDraftCreate,
    current_user=Depends(get_current_user),
    service: SalesPlaybookGenerationJobService = Depends(get_generation_job_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        job = service.create_or_reuse(
            workspace_id=workspace_id, requested_by=user_id, data=data,
        )
    except (GenerationJobIdempotencyConflict, IdempotencyConflict) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SalesPlaybookGenerationJobRead.from_job(job)


@router.get("/generation-jobs/active", response_model=SalesPlaybookGenerationJobRead | None)
def get_active_generation_job(
    current_user=Depends(get_current_user),
    service: SalesPlaybookGenerationJobService = Depends(get_generation_job_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    job = service.active(workspace_id)
    return SalesPlaybookGenerationJobRead.from_job(job) if job else None


@router.get("/generation-jobs/relevant", response_model=SalesPlaybookGenerationJobRead | None)
def get_relevant_generation_job(
    current_user=Depends(get_current_user),
    service: SalesPlaybookGenerationJobService = Depends(get_generation_job_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    job = service.latest_relevant(workspace_id)
    return SalesPlaybookGenerationJobRead.from_job(job) if job else None


@router.get("/generation-jobs/{job_id}", response_model=SalesPlaybookGenerationJobRead)
def get_generation_job(
    job_id: UUID,
    current_user=Depends(get_current_user),
    service: SalesPlaybookGenerationJobService = Depends(get_generation_job_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    job = service.get(workspace_id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found")
    return SalesPlaybookGenerationJobRead.from_job(job)


@router.post("/generation-jobs/{job_id}/retry", response_model=SalesPlaybookGenerationJobRead,
             status_code=status.HTTP_202_ACCEPTED)
def retry_generation_job(
    job_id: UUID,
    current_user=Depends(get_current_user),
    service: SalesPlaybookGenerationJobService = Depends(get_generation_job_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        job = service.retry_failed(
            workspace_id=workspace_id, requested_by=user_id, job_id=job_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return SalesPlaybookGenerationJobRead.from_job(job)


@router.post(
    "/generation-jobs/{job_id}/resume",
    response_model=SalesPlaybookGenerationJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_generation_job(
    job_id: UUID,
    current_user=Depends(get_current_user),
    service: SalesPlaybookGenerationJobService = Depends(get_generation_job_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        job = service.resume_failed(
            workspace_id=workspace_id, requested_by=user_id, source_job_id=job_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found") from exc
    except GenerationResumeRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Generation job cannot be resumed") from exc
    return SalesPlaybookGenerationJobRead.from_job(job)


@router.get("/active", response_model=SalesPlaybookRead)
def get_active_playbook(
    current_user=Depends(get_current_user),
    service: SalesPlaybookService = Depends(get_read_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    playbook = service.get_active(workspace_id)
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved sales playbook not found")
    return _read(playbook)


@router.get("/{playbook_id}", response_model=SalesPlaybookRead)
def get_playbook(
    playbook_id: UUID,
    current_user=Depends(get_current_user),
    service: SalesPlaybookService = Depends(get_read_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    playbook = service.get(playbook_id, workspace_id)
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)


@router.post("/commercial-context/drafts", response_model=SalesPlaybookRead, status_code=status.HTTP_201_CREATED)
def create_commercial_context_draft(
    data: CommercialContextDraftCreate,
    request: Request,
    current_user=Depends(get_current_user),
    service: CommercialContextService = Depends(get_commercial_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    from app.modules.demo.errors import DemoError

    if (getattr(request.state, "demo_principal", None) is not None
            and (data.assisted or data.source_document_ids)):
        raise DemoError("demo_route_unavailable", "La demo usa condiciones editables sin generación asistida.", 403)
    try:
        playbook = service.create_draft(
            workspace_id=workspace_id,
            content=data.content,
            pasted_text=data.pasted_text,
            source_document_ids=data.source_document_ids,
            idempotency_key=data.idempotency_key,
            assisted=data.assisted,
        )
    except CommercialIdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        if "exceeds 16,000 characters" in str(exc):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Assisted suggestion is unavailable; no draft was saved",
        ) from exc
    return _read(playbook)


@router.patch("/{playbook_id}/commercial-context", response_model=SalesPlaybookRead)
def update_commercial_context_draft(
    playbook_id: UUID,
    data: CommercialContextDraftUpdate,
    current_user=Depends(get_current_user),
    service: CommercialContextService = Depends(get_commercial_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        playbook = service.update_draft(
            playbook_id=playbook_id,
            workspace_id=workspace_id,
            expected_digest=data.expected_digest,
            content=data.content,
            edited_by=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)


@router.post("/{playbook_id}/approve", response_model=SalesPlaybookRead)
def approve_playbook(
    playbook_id: UUID,
    data: SalesPlaybookApprove | None = None,
    current_user=Depends(require_playbook_approver),
    db: Session = Depends(get_db),
):
    user_id, workspace_id, _ = current_user
    require_member(db, user_id, workspace_id)
    # Approval validates the stored contract version under the workspace lock;
    # this service is constructed without the legacy provider factory.
    service = SalesPlaybookService(db, generator=None)
    try:
        playbook = service.approve(
            playbook_id,
            workspace_id,
            user_id,
            data.reviewed_guardrail_assertion_ids if data else None,
            data.content_digest if data else None,
            commercial_confirmed=data.confirmed if data else False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)


@router.post(
    "/{playbook_id}/workflow-conflicts/{conflict_id}/resolve",
    response_model=SalesPlaybookRead,
)
def resolve_workflow_conflict(
    playbook_id: UUID,
    conflict_id: str,
    data: SalesPlaybookWorkflowConflictResolve,
    current_user=Depends(require_playbook_approver),
    service: SalesPlaybookService = Depends(get_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        playbook = service.resolve_workflow_conflict(
            playbook_id, workspace_id, user_id, conflict_id, data.candidate_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)


@router.post(
    "/{playbook_id}/workflow-transitions/complete",
    response_model=SalesPlaybookRead,
)
def complete_workflow_transition(
    playbook_id: UUID,
    data: SalesPlaybookWorkflowTransitionComplete,
    current_user=Depends(require_playbook_approver),
    service: SalesPlaybookService = Depends(get_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        playbook = service.complete_workflow_transition(playbook_id, workspace_id, user_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)


@router.post(
    "/{playbook_id}/semantic-workflow-findings/{finding_id}/review",
    response_model=SalesPlaybookRead,
)
def review_semantic_workflow_finding(
    playbook_id: UUID,
    finding_id: str,
    current_user=Depends(require_playbook_approver),
    service: SalesPlaybookService = Depends(get_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        playbook = service.review_semantic_workflow_finding(
            playbook_id, workspace_id, user_id, finding_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)


@router.post("/{playbook_id}/archive", response_model=SalesPlaybookRead)
def archive_playbook(
    playbook_id: UUID,
    current_user=Depends(require_playbook_approver),
    service: SalesPlaybookService = Depends(get_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    playbook = service.archive(playbook_id, workspace_id)
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)


@router.post("/{playbook_id}/reject", response_model=SalesPlaybookRead)
def reject_playbook(
    playbook_id: UUID,
    data: SalesPlaybookReject,
    current_user=Depends(require_playbook_approver),
    service: SalesPlaybookService = Depends(get_service),
):
    user_id, workspace_id, _ = current_user
    require_member(service.db, user_id, workspace_id)
    try:
        playbook = service.reject(playbook_id, workspace_id, user_id, data.reason)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if playbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sales playbook not found")
    return _read(playbook)
