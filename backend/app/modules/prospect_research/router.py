from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.prospect_research.models import ProspectResearch, ProspectResearchStatus
from app.modules.prospect_research.schemas import ProspectResearchRead, ResearchEvidenceRead
from app.modules.prospect_research.service import ProspectResearchService
from app.workers.queue import enqueue_prospect_research

router = APIRouter(prefix="/leads/{lead_id}/research", tags=["prospect-research"])
require_research_view = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])


def get_research_service(db: Session = Depends(get_db)) -> ProspectResearchService:
    return ProspectResearchService(db)


def _trigger(
    lead_id: UUID,
    workspace_id: UUID,
    service: ProspectResearchService,
    force_refresh: bool,
) -> ProspectResearchRead:
    try:
        research, should_enqueue = service.start_research(
            lead_id, workspace_id, force_refresh=force_refresh
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    job_id = None
    if should_enqueue:
        service.db.commit()
        try:
            job_id = enqueue_prospect_research(str(research.id))
        except Exception as exc:
            service.db.rollback()
            failed = service.db.get(ProspectResearch, research.id)
            if failed:
                failed.status = ProspectResearchStatus.FAILED
                failed.error = "No se pudo encolar la investigación"
                service.db.commit()
            raise HTTPException(status_code=503, detail="No se pudo iniciar la investigación") from exc
    return ProspectResearchRead.model_validate(research).model_copy(update={"job_id": job_id})


@router.post("", response_model=ProspectResearchRead, status_code=status.HTTP_202_ACCEPTED)
def trigger_research(
    lead_id: UUID,
    service: ProspectResearchService = Depends(get_research_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_research_view),
) -> ProspectResearchRead:
    return _trigger(lead_id, current_user[1], service, False)


@router.post("/refresh", response_model=ProspectResearchRead, status_code=status.HTTP_202_ACCEPTED)
def refresh_research(
    lead_id: UUID,
    service: ProspectResearchService = Depends(get_research_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_research_view),
) -> ProspectResearchRead:
    return _trigger(lead_id, current_user[1], service, True)


@router.get("", response_model=ProspectResearchRead | None)
def latest_research(
    lead_id: UUID,
    service: ProspectResearchService = Depends(get_research_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_research_view),
) -> ProspectResearchRead | None:
    return service.get_latest(lead_id, current_user[1])


@router.get("/evidence", response_model=list[ResearchEvidenceRead])
def research_evidence(
    lead_id: UUID,
    research_id: UUID | None = None,
    service: ProspectResearchService = Depends(get_research_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_research_view),
) -> list[ResearchEvidenceRead]:
    return service.list_evidence(lead_id, current_user[1], research_id)
