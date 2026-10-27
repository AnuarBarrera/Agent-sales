"""Authenticated, tenant-scoped Setter execution and review reads."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import (
    NotAuthorizedError,
    can_view_lead,
    require_member,
    require_role_dependency,
)
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.leads.models import Lead
from app.modules.prospect_research.schemas import ResearchEvidenceRead
from app.modules.setter.context import SetterPreconditionError
from app.modules.setter.generator import SetterGenerationError
from app.modules.setter.models import SetterExecution
from app.modules.setter.schemas import (
    SetterDecision,
    SetterDraftReadinessRead,
    SetterExecutionRead,
    SetterValidationFinding,
    Touch1Draft,
)
from app.modules.setter.service import (
    SetterEvidenceUnavailable,
    SetterOutputValidationError,
    SetterService,
    list_execution_evidence,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/leads/{lead_id}/setter", tags=["setter"])
require_setter_access = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])


def get_setter_service(db: Session = Depends(get_db)) -> SetterService:
    return SetterService(db)


def _require_lead_access(db: Session, lead_id: UUID, user_id: UUID, workspace_id: UUID):
    lead = db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    try:
        membership = require_member(db, user_id, workspace_id)
    except NotAuthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized") from exc
    if not can_view_lead(membership, lead):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this lead")
    return lead


def _read(execution: SetterExecution) -> SetterExecutionRead:
    decision = SetterDecision.model_validate(execution.decision)
    draft = Touch1Draft.model_validate(execution.draft) if execution.draft is not None else None
    validation = execution.validation_result
    return SetterExecutionRead(
        id=execution.id,
        workspace_id=execution.workspace_id,
        lead_id=execution.lead_id,
        decision=decision,
        touch1_draft=draft,
        validation_status=validation["status"],
        validation_findings=[SetterValidationFinding.model_validate(item) for item in validation["findings"]],
        provider=execution.provider,
        model=execution.model,
        review_status=execution.review_status,
        created_at=execution.created_at,
    )


@router.post("/execute", response_model=SetterExecutionRead)
def execute_setter(
    lead_id: UUID,
    db: Session = Depends(get_db),
    service: SetterService = Depends(get_setter_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_setter_access),
) -> SetterExecutionRead:
    user_id, workspace_id, _ = current_user
    _require_lead_access(db, lead_id, user_id, workspace_id)
    try:
        execution = service.execute(lead_id, workspace_id, user_id)
        db.commit()
        return _read(execution)
    except SetterPreconditionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SetterGenerationError as exc:
        db.rollback()
        audit = exc.audit
        logger.warning(
            "setter_api_failure phase=%s provider=%s model=%s http_status=%s finish_reason=%s latency_ms=%s",
            exc.phase,
            audit.provider if audit else None,
            audit.model if audit else None,
            audit.http_status if audit else None,
            audit.finish_reason if audit else None,
            audit.latency_ms if audit else None,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Setter generation failed; no execution was saved.",
        ) from exc
    except SetterOutputValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Setter output failed deterministic safety validation.",
        ) from exc
    except RuntimeError as exc:
        db.rollback()
        logger.warning("setter_api_failure phase=provider_configuration exception=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Setter provider is unavailable or not configured.",
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/readiness", response_model=SetterDraftReadinessRead)
def setter_draft_readiness(
    lead_id: UUID,
    db: Session = Depends(get_db),
    service: SetterService = Depends(get_setter_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_setter_access),
) -> SetterDraftReadinessRead:
    user_id, workspace_id, _ = current_user
    lead = _require_lead_access(db, lead_id, user_id, workspace_id)
    if lead.contacted or lead.first_contacted_at is not None:
        return SetterDraftReadinessRead(
            draft_ready=False, reason_codes=["evaluation_unavailable"],
            identity_status="unresolved", contact_channels=[], lead_id=lead_id,
        )
    return service.preview_draft_readiness(lead_id, workspace_id)


@router.get("/latest", response_model=SetterExecutionRead | None)
def latest_setter_execution(
    lead_id: UUID,
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_setter_access),
) -> SetterExecutionRead | None:
    user_id, workspace_id, _ = current_user
    _require_lead_access(db, lead_id, user_id, workspace_id)
    execution = db.execute(
        select(SetterExecution)
        .where(SetterExecution.workspace_id == workspace_id, SetterExecution.lead_id == lead_id)
        .order_by(SetterExecution.created_at.desc(), SetterExecution.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _read(execution) if execution is not None else None


@router.get("/executions/{execution_id}", response_model=SetterExecutionRead)
def get_setter_execution(
    lead_id: UUID,
    execution_id: UUID,
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_setter_access),
) -> SetterExecutionRead:
    user_id, workspace_id, _ = current_user
    _require_lead_access(db, lead_id, user_id, workspace_id)
    execution = db.execute(
        select(SetterExecution).where(
            SetterExecution.id == execution_id,
            SetterExecution.workspace_id == workspace_id,
            SetterExecution.lead_id == lead_id,
        )
    ).scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setter execution not found")
    return _read(execution)


@router.get(
    "/executions/{execution_id}/evidence",
    response_model=list[ResearchEvidenceRead],
)
def get_setter_execution_evidence(
    lead_id: UUID,
    execution_id: UUID,
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_setter_access),
) -> list[ResearchEvidenceRead]:
    user_id, workspace_id, _ = current_user
    _require_lead_access(db, lead_id, user_id, workspace_id)
    execution = db.execute(
        select(SetterExecution).where(
            SetterExecution.id == execution_id,
            SetterExecution.workspace_id == workspace_id,
            SetterExecution.lead_id == lead_id,
        )
    ).scalar_one_or_none()
    if execution is None or (
        execution.id != execution_id
        or execution.workspace_id != workspace_id
        or execution.lead_id != lead_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setter execution not found")
    try:
        rows = list_execution_evidence(db, execution)
    except SetterEvidenceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setter evidence is unavailable for this execution.",
        ) from exc
    return [ResearchEvidenceRead.model_validate(row) for row in rows]
