"""Router de Lead Fit / Jev (T03).

Rutas mínimas de política de encaje. Endpoints de evaluación e investigación se
agregan en T05/T07. Todas las rutas requieren autenticación y membresía.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import NotAuthorizedError, can_view_lead, require_member
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.identity.router import get_current_user
from app.modules.lead_fit.models import (
    LeadFitEvaluation,
    LeadFitEvaluationStatus,
    LeadFitPolicy,
    LeadFitPolicyStatus,
)
from app.modules.lead_fit.schemas import (
    FitEvaluationContext,
    FitEvaluationCreate,
    FitEvaluationRead,
    FitPolicyCreate,
    FitPolicyPreview,
    FitPolicyPreviewRequest,
    FitPolicyRead,
    FitResearchAuthorize,
    FitResearchGrantRead,
    FitResearchPlanSchema,
    FitResearchPreviewRequest,
)
from app.modules.lead_fit.service import (
    LeadFitPolicyService,
    PolicyAuthorityNotFound,
    PolicyAuthorityStale,
)
from app.modules.leads.models import Lead
from app.modules.prospection.models import ProspectionRun
from app.modules.sales_playbook.commercial_service import CommercialContextService


def require_lead_fit_enabled() -> None:
    if not get_settings().lead_fit_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead Fit no está habilitado",
        )


router = APIRouter(prefix="/lead-fit", tags=["lead-fit"])
evaluation_router = APIRouter(tags=["lead-fit"])

def require_policy_role(allowed_roles: set[UserRole]):
    """Require a fresh active workspace membership and an allowed member role."""
    def role_checker(
        db: Session = Depends(get_db),
        current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user),
    ) -> tuple[UUID, UUID, UserRole]:
        user_id, workspace_id, _token_role = current_user
        try:
            membership = require_member(db, user_id, workspace_id)
        except NotAuthorizedError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active workspace membership required",
            ) from exc
        if membership.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user_id, workspace_id, membership.role

    return role_checker


require_admin = require_policy_role({UserRole.ADMIN})
require_policy_reader = require_policy_role(
    {UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER}
)


def get_policy_service(db: Session = Depends(get_db)) -> LeadFitPolicyService:
    return LeadFitPolicyService(db)


def get_evaluation_service(db: Session = Depends(get_db)):
    from app.modules.lead_fit.evaluation_service import LeadFitEvaluationService

    return LeadFitEvaluationService(db)


def get_research_service(db: Session = Depends(get_db)):
    from app.modules.lead_fit.research import LeadFitResearchService

    return LeadFitResearchService(db)


@router.get("/capabilities")
def lead_fit_capabilities(
    _member: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
) -> dict[str, bool]:
    """Authenticated UI capability read; no provider or Lead Fit table access."""
    return {"enabled": bool(get_settings().lead_fit_enabled)}


@router.post(
    "/policies/preview",
    response_model=FitPolicyPreview,
    status_code=status.HTTP_200_OK,
)
def preview_policy(
    payload: FitPolicyPreviewRequest,
    service: LeadFitPolicyService = Depends(get_policy_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitPolicyPreview:
    """
    Propuesta determinista de política desde el contexto comercial vigente.

    Cero proveedores. Devuelve contenido y digest esperado, sin aprobar.
    """
    _, workspace_id, _ = current_user
    try:
        result = service.preview(
            workspace_id=workspace_id,
            playbook_id=payload.playbook_id,
        )
    except PolicyAuthorityNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved playbook not found") from exc
    except PolicyAuthorityStale as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approved commercial context requires review") from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Policy authority is temporarily unavailable") from exc
    return FitPolicyPreview(
        content=result["content"],
        expected_policy_digest=result["expected_policy_digest"],
        expected_commercial_digest=result["expected_commercial_digest"],
    )


@router.post(
    "/policies",
    response_model=FitPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
def approve_policy(
    payload: FitPolicyCreate,
    service: LeadFitPolicyService = Depends(get_policy_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitPolicyRead:
    """
    Aprueba una política de encaje (ADMIN).

    Crea una versión append-only ligada al digest comercial 4.0 vigente.
    Actor y timestamp son server-owned (sesión).
    """
    user_id, workspace_id, role = current_user
    try:
        policy = service.approve(
            workspace_id=workspace_id,
            actor_id=user_id,
            actor_role=role,
            playbook_id=payload.playbook_id,
            content=payload.content,
            expected_commercial_digest=payload.expected_commercial_digest,
            confirmed=payload.confirmed,
        )
    except PolicyAuthorityNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved playbook not found") from exc
    except PolicyAuthorityStale as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Commercial authority changed; refresh the proposal") from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Policy authority is temporarily unavailable") from exc
    return FitPolicyRead.model_validate(policy)


@router.get(
    "/policies/current",
    response_model=FitPolicyRead | None,
    status_code=status.HTTP_200_OK,
)
def current_policy(
    playbook_id: UUID,
    service: LeadFitPolicyService = Depends(get_policy_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitPolicyRead | None:
    """Devuelve la política vigente aprobada para un playbook/workspace, o None."""
    _, workspace_id, _ = current_user
    try:
        policy = service.current(workspace_id=workspace_id, playbook_id=playbook_id)
    except PolicyAuthorityNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved playbook not found") from exc
    except PolicyAuthorityStale:
        return None
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Policy authority is temporarily unavailable") from exc
    if policy is None:
        return None
    return FitPolicyRead.model_validate(policy)


def _authorized_lead(service, lead_id: UUID, user: tuple[UUID, UUID, UserRole]) -> Lead:
    user_id, workspace_id, role = user
    lead = service.db.execute(select(Lead).where(
        Lead.workspace_id == workspace_id, Lead.id == lead_id,
    )).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    membership = type("ActiveMember", (), {"user_id": user_id, "role": role})()
    if not can_view_lead(membership, lead):
        raise HTTPException(status_code=403, detail="Not authorized to view this lead")
    return lead


def _lead_policy_id(service, lead: Lead, workspace_id: UUID) -> UUID:
    if lead.source_run_id is None:
        raise HTTPException(status_code=409, detail="Lead has no approved prospection policy")
    run = service.db.execute(select(ProspectionRun).where(
        ProspectionRun.workspace_id == workspace_id,
        ProspectionRun.id == lead.source_run_id,
    )).scalar_one_or_none()
    if run is None or run.fit_policy_id is None:
        raise HTTPException(status_code=409, detail="Lead has no approved prospection policy")
    frozen_policy = service.db.execute(select(LeadFitPolicy).where(
        LeadFitPolicy.workspace_id == workspace_id,
        LeadFitPolicy.id == run.fit_policy_id,
    )).scalar_one_or_none()
    if frozen_policy is None:
        raise HTTPException(status_code=409, detail="Lead policy is unavailable")
    policy_service = getattr(service, "policy_service", None)
    if policy_service is None:
        raise HTTPException(status_code=503, detail="Current policy authority is unavailable")
    try:
        active_context = CommercialContextService(service.db).get_active(workspace_id)
    except MultipleResultsFound as exc:
        raise HTTPException(status_code=409, detail="Current approved context is unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Current policy authority is unavailable") from exc
    if active_context is None:
        raise HTTPException(status_code=409, detail="Current approved context is unavailable")
    try:
        # current() verifies the active approved commercial source and returns only
        # its fresh, workspace-scoped policy. The run's frozen policy remains the
        # immutable record of discovery authority; it does not choose current policy.
        current_policy = policy_service.current(workspace_id, active_context.id)
    except (PolicyAuthorityNotFound, PolicyAuthorityStale) as exc:
        raise HTTPException(status_code=409, detail="Current approved policy is unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Current policy authority is unavailable") from exc
    if (
        current_policy is None
        or current_policy.workspace_id != workspace_id
        or current_policy.playbook_id != active_context.id
        or current_policy.status != LeadFitPolicyStatus.APPROVED
    ):
        raise HTTPException(status_code=409, detail="Current approved policy is unavailable")
    return current_policy.id


@evaluation_router.get("/leads/{lead_id}/fit-evaluations/context", response_model=FitEvaluationContext)
def evaluation_context(
    lead_id: UUID,
    service=Depends(get_evaluation_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitEvaluationContext:
    _, workspace_id, _ = current_user
    lead = _authorized_lead(service, lead_id, current_user)
    policy_id = _lead_policy_id(service, lead, workspace_id)
    policy = service.db.execute(select(LeadFitPolicy).where(
        LeadFitPolicy.workspace_id == workspace_id,
        LeadFitPolicy.id == policy_id,
    )).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=409, detail="Lead policy is unavailable")
    try:
        context = service.context(
            workspace_id=workspace_id, lead_id=lead_id, playbook_id=policy.playbook_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Policy or source snapshot changed") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Evaluation context is temporarily unavailable") from exc
    if context["policy_id"] != policy_id:
        raise HTTPException(status_code=409, detail="Current policy changed")
    return FitEvaluationContext(**context)


@evaluation_router.post(
    "/leads/{lead_id}/fit-evaluations",
    response_model=FitEvaluationRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_fit_evaluation(
    lead_id: UUID,
    payload: FitEvaluationCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    service=Depends(get_evaluation_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitEvaluationRead:
    user_id, workspace_id, _ = current_user
    lead = _authorized_lead(service, lead_id, current_user)
    policy_id = _lead_policy_id(service, lead, workspace_id)
    try:
        evaluation = service.request(
            workspace_id=workspace_id, lead_id=lead_id, user_id=user_id,
            policy_id=policy_id,
            expected_policy_digest=payload.expected_policy_digest,
            expected_source_digest=payload.expected_source_digest,
            idempotency_key=idempotency_key,
            retry_of=payload.retry_of,
            reenqueue=payload.reenqueue,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Evaluation request is temporarily unavailable") from exc

    response_snapshot = FitEvaluationRead.model_validate(evaluation)
    evaluation_id = evaluation.id
    if evaluation.status.value == "pending":
        # A worker must observe the committed intent before it starts.
        service.db.commit()
        try:
            from app.modules.lead_fit.search_flow import enqueue_lead_fit_evaluation

            allow_terminal_reenqueue = service.consume_queue_reenqueue_permission(evaluation_id)
            enqueue_lead_fit_evaluation(
                str(evaluation_id),
                allow_terminal_reenqueue=allow_terminal_reenqueue,
            )
        except Exception as exc:
            service.mark_enqueue_failed(evaluation_id)
            raise HTTPException(status_code=503, detail="Evaluation queue is unavailable") from exc
    return response_snapshot


@evaluation_router.get("/leads/{lead_id}/fit-evaluations/latest", response_model=FitEvaluationRead | None)
def latest_fit_evaluation(
    lead_id: UUID,
    service=Depends(get_evaluation_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitEvaluationRead | None:
    _, workspace_id, _ = current_user
    _authorized_lead(service, lead_id, current_user)
    evaluation = service.latest(workspace_id, lead_id)
    return FitEvaluationRead.model_validate(evaluation) if evaluation else None


@evaluation_router.post(
    "/leads/{lead_id}/fit-research/preview",
    response_model=FitResearchPlanSchema,
    status_code=status.HTTP_200_OK,
)
def preview_fit_research(
    lead_id: UUID,
    payload: FitResearchPreviewRequest,
    service=Depends(get_research_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitResearchPlanSchema:
    _, workspace_id, _ = current_user
    _authorized_lead(service, lead_id, current_user)
    evaluation = service.db.execute(select(LeadFitEvaluation).where(
        LeadFitEvaluation.workspace_id == workspace_id,
        LeadFitEvaluation.lead_id == lead_id,
        LeadFitEvaluation.status == LeadFitEvaluationStatus.COMPLETED,
    ).order_by(LeadFitEvaluation.requested_at.desc()).limit(1)).scalar_one_or_none()
    if evaluation is None:
        raise HTTPException(status_code=409, detail="A completed evaluation is required before research")
    try:
        plan = service.preview(
            workspace_id=workspace_id, lead_id=lead_id,
            evaluation_id=evaluation.id, purpose=payload.purpose,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Research preview is stale or unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Research preview is temporarily unavailable") from exc
    return FitResearchPlanSchema.model_validate(plan)


@evaluation_router.post(
    "/leads/{lead_id}/fit-research",
    response_model=FitResearchGrantRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def authorize_fit_research(
    lead_id: UUID,
    payload: FitResearchAuthorize,
    evaluation_id: UUID,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    service=Depends(get_research_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitResearchGrantRead:
    user_id, workspace_id, _ = current_user
    _authorized_lead(service, lead_id, current_user)
    try:
        grant = service.authorize(
            workspace_id=workspace_id, user_id=user_id,
            lead_id=lead_id, evaluation_id=evaluation_id,
            purpose=payload.purpose,
            expected_plan_hash=payload.expected_plan_hash,
            confirmed=payload.confirmed,
            idempotency_key=idempotency_key,
            monetary_cap_usd=payload.monetary_cap_usd,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Research authorization is temporarily unavailable") from exc
    fresh = grant.status.value == "authorized" and grant.dispatched_at is None
    if fresh:
        # Durable queue-dispatch marker makes an HTTP replay incapable of
        # creating a replacement job after its original receipt disappears.
        grant.dispatched_at = datetime.now(UTC)
    response_snapshot = FitResearchGrantRead.model_validate(grant)
    service.db.commit()
    if fresh:
        try:
            from app.workers.queue import enqueue_lead_fit_research

            enqueue_lead_fit_research(str(grant.id))
        except Exception as exc:
            service.mark_enqueue_failed(grant.id)
            raise HTTPException(status_code=503, detail="Research queue is unavailable") from exc
    return response_snapshot


@evaluation_router.get(
    "/leads/{lead_id}/fit-research/{grant_id}",
    response_model=FitResearchGrantRead | None,
    status_code=status.HTTP_200_OK,
)
def get_fit_research_status(
    lead_id: UUID,
    grant_id: UUID,
    service=Depends(get_research_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_policy_reader),
    _enabled: None = Depends(require_lead_fit_enabled),
) -> FitResearchGrantRead | None:
    _, workspace_id, _ = current_user
    _authorized_lead(service, lead_id, current_user)
    grant = service.status(workspace_id=workspace_id, lead_id=lead_id, grant_id=grant_id)
    return FitResearchGrantRead.model_validate(grant) if grant else None
