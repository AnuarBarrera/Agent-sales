from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import require_role_dependency
from app.db.session import get_db
from app.modules.identity.models import UserRole
from app.modules.prospect_analysis.schemas import (
    ProspectAnalysisRead,
    ProspectIntelligenceRead,
    QualificationRead,
)
from app.modules.prospect_analysis.service import (
    ProspectAnalysisPrerequisiteError,
    ProspectAnalysisService,
    ProspectAnalysisValidationError,
)

router = APIRouter(prefix="/leads/{lead_id}/analysis", tags=["prospect-analysis"])
require_analysis_view = require_role_dependency([UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER])


def get_analysis_service(db: Session = Depends(get_db)) -> ProspectAnalysisService:
    return ProspectAnalysisService(db)


def _analyze(
    lead_id: UUID,
    workspace_id: UUID,
    service: ProspectAnalysisService,
    force_refresh: bool,
) -> ProspectAnalysisRead:
    try:
        result = service.analyze(lead_id, workspace_id, force_refresh=force_refresh)
        service.db.commit()
        return ProspectAnalysisRead.model_validate(result)
    except ProspectAnalysisPrerequisiteError as exc:
        service.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ProspectAnalysisValidationError as exc:
        service.db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("", response_model=ProspectAnalysisRead)
def analyze_prospect(
    lead_id: UUID,
    service: ProspectAnalysisService = Depends(get_analysis_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_analysis_view),
) -> ProspectAnalysisRead:
    return _analyze(lead_id, current_user[1], service, False)


@router.post("/reanalyze", response_model=ProspectAnalysisRead)
def reanalyze_prospect(
    lead_id: UUID,
    service: ProspectAnalysisService = Depends(get_analysis_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_analysis_view),
) -> ProspectAnalysisRead:
    return _analyze(lead_id, current_user[1], service, True)


@router.get("", response_model=ProspectAnalysisRead | None)
def latest_analysis(
    lead_id: UUID,
    service: ProspectAnalysisService = Depends(get_analysis_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_analysis_view),
) -> ProspectAnalysisRead | None:
    return service.get_latest(lead_id, current_user[1])


@router.get("/intelligence", response_model=ProspectIntelligenceRead)
def latest_intelligence(
    lead_id: UUID,
    service: ProspectAnalysisService = Depends(get_analysis_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_analysis_view),
) -> ProspectIntelligenceRead:
    result = service.get_latest_successful(lead_id, current_user[1])
    if not result or not result.intelligence:
        raise HTTPException(status_code=404, detail="Prospect intelligence not found")
    return ProspectIntelligenceRead(
        analysis_id=result.id,
        sales_playbook_id=result.sales_playbook_id,
        research_id=result.research_id,
        provider=result.intelligence_provider,
        model=result.intelligence_model,
        prompt_version=result.intelligence_prompt_version,
        contract_version=result.intelligence_contract_version,
        generated_at=result.generated_at,
        intelligence=result.intelligence,
    )


@router.get("/qualification", response_model=QualificationRead)
def latest_qualification(
    lead_id: UUID,
    service: ProspectAnalysisService = Depends(get_analysis_service),
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_analysis_view),
) -> QualificationRead:
    result = service.get_latest_successful(lead_id, current_user[1])
    if not result or not result.qualification:
        raise HTTPException(status_code=404, detail="Qualification not found")
    return QualificationRead(
        analysis_id=result.id,
        sales_playbook_id=result.sales_playbook_id,
        research_id=result.research_id,
        provider=result.qualification_provider,
        model=result.qualification_model,
        prompt_version=result.qualification_prompt_version,
        contract_version=result.qualification_contract_version,
        generated_at=result.generated_at,
        qualification=result.qualification,
    )
