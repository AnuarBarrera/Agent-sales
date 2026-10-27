"""Authenticated demo status and bounded business operations."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.demo.access import load_demo_principal
from app.modules.demo.errors import DemoError
from app.modules.demo.models import DemoParticipant
from app.modules.demo.quota import DemoQuotaRepository
from app.modules.demo.schemas import (
    DemoCapabilities,
    DemoDraftApprovalRequest,
    DemoDraftRequest,
    DemoDraftReviewRead,
    DemoFollowUpCompleteRequest,
    DemoFollowUpContactRequest,
    DemoFollowUpNoteRequest,
    DemoFollowUpRead,
    DemoFollowUpTaskRequest,
    DemoSearchQuota,
    DemoSessionRead,
)
from app.modules.demo.service import DemoDraftService, DemoSearchService
from app.modules.identity.models import UserRole
from app.modules.identity.router import get_current_user
from app.modules.lead_fit.service import LeadFitPolicyService
from app.modules.prospection.router import serialize_run
from app.modules.prospection.schemas import ProspectionRunCreate, ProspectionRunRead
from app.modules.sales_playbook.commercial_service import CommercialContextService
from app.modules.setter.router import _read as read_execution
from app.modules.setter.schemas import SetterExecutionRead

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/leads/{lead_id}/follow-up", response_model=DemoFollowUpRead)
def read_demo_follow_up(lead_id: UUID, db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    from app.modules.demo.follow_up import DemoFollowUpService
    return DemoFollowUpService(db).read(principal=_principal(db, current_user), lead_id=lead_id)


@router.post("/leads/{lead_id}/follow-up/notes", response_model=DemoFollowUpRead)
def add_demo_note(lead_id: UUID, payload: DemoFollowUpNoteRequest, db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    from app.modules.demo.follow_up import DemoFollowUpService
    return DemoFollowUpService(db).note(principal=_principal(db, current_user), lead_id=lead_id, payload=payload)


@router.post("/leads/{lead_id}/follow-up/contacts", response_model=DemoFollowUpRead)
def record_demo_contact(lead_id: UUID, payload: DemoFollowUpContactRequest, db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    from app.modules.demo.follow_up import DemoFollowUpService
    return DemoFollowUpService(db).contact(principal=_principal(db, current_user), lead_id=lead_id, payload=payload)


@router.post("/leads/{lead_id}/follow-up/tasks", response_model=DemoFollowUpRead)
def schedule_demo_follow_up(lead_id: UUID, payload: DemoFollowUpTaskRequest, db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    from app.modules.demo.follow_up import DemoFollowUpService
    return DemoFollowUpService(db).schedule(principal=_principal(db, current_user), lead_id=lead_id, payload=payload)


@router.post("/leads/{lead_id}/follow-up/tasks/{task_id}/complete", response_model=DemoFollowUpRead)
def complete_demo_follow_up(lead_id: UUID, task_id: UUID, payload: DemoFollowUpCompleteRequest,
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    from app.modules.demo.follow_up import DemoFollowUpService
    return DemoFollowUpService(db).complete(principal=_principal(db, current_user), lead_id=lead_id,
        task_id=task_id, payload=payload)


@router.get("/leads/{lead_id}/draft-review", response_model=DemoDraftReviewRead)
def get_demo_draft_review(lead_id: UUID, execution_id: UUID,
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    from app.modules.demo.draft_review import DemoDraftReviewService
    return DemoDraftReviewService(db).preview(principal=_principal(db, current_user),
        lead_id=lead_id, execution_id=execution_id)


@router.post("/leads/{lead_id}/draft-review", response_model=DemoDraftReviewRead)
def approve_demo_draft(lead_id: UUID, payload: DemoDraftApprovalRequest,
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    from app.modules.demo.draft_review import DemoDraftReviewService
    return DemoDraftReviewService(db).approve(principal=_principal(db, current_user),
        lead_id=lead_id, payload=payload)


def get_search_service(db: Session = Depends(get_db)):
    return DemoSearchService(db)


def get_draft_service(db: Session = Depends(get_db)):
    return DemoDraftService(db)


def _principal(db, current_user):
    principal = load_demo_principal(db, current_user[0], current_user[1])
    if principal is None:
        raise DemoError("demo_participant_required", "Entra a la demo con tu correo verificado.", 403)
    return principal


@router.get("/session", response_model=DemoSessionRead)
def demo_session(db: Session = Depends(get_db), current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    principal = load_demo_principal(db, current_user[0], current_user[1])
    if principal is None:
        return DemoSessionRead(enabled=False, workspace_id=current_user[1], template_version="",
            review_required=False, searches=DemoSearchQuota(used=0, remaining=2),
            global_search_available=False, capabilities=DemoCapabilities())
    repo = DemoQuotaRepository(db)
    participant = db.execute(select(DemoParticipant).where(
        DemoParticipant.id == principal.participant_id,
    )).scalar_one()
    used = repo.count_searches(principal.participant_id)
    active = CommercialContextService(db).get_active(principal.workspace_id)
    policy = LeadFitPolicyService(db).current(principal.workspace_id, active.id) if active else None
    return DemoSessionRead(enabled=True, workspace_id=principal.workspace_id,
        template_version=participant.template_version, review_required=policy is None,
        searches=DemoSearchQuota(used=used, remaining=max(0, 2 - used)),
        global_search_available=(get_settings().demo_public_mode
            and repo.count_searches() < get_settings().demo_global_search_limit),
        capabilities=DemoCapabilities())


@router.post("/searches", response_model=ProspectionRunRead, status_code=202)
def create_demo_search(payload: ProspectionRunCreate,
    idempotency_key: UUID = Header(..., alias="Idempotency-Key"),
    service=Depends(get_search_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    principal = _principal(service.db, current_user)
    return serialize_run(service.create_search(principal=principal, payload=payload, idempotency_key=idempotency_key))


@router.post("/leads/{lead_id}/draft", response_model=SetterExecutionRead)
def create_demo_draft(lead_id: UUID, payload: DemoDraftRequest,
    idempotency_key: UUID = Header(..., alias="Idempotency-Key"),
    service=Depends(get_draft_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)):
    principal = _principal(service.db, current_user)
    return read_execution(service.create_or_reuse_draft(principal=principal, lead_id=lead_id,
        channel=payload.channel, idempotency_key=idempotency_key))
