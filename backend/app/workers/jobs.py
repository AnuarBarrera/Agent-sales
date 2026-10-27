import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import select

from app.ai.validation import AIContractValidationError
from app.core.config import get_settings

# Register all relationship targets before SQLAlchemy configures mappers.
from app.db import models as _models  # noqa: F401
from app.db.session import SessionLocal
from app.modules.prospect_research.service import ProspectResearchService
from app.modules.prospection.models import RunLead
from app.modules.prospection.service import ProspectionService
from app.modules.sales_playbook.document_models import SourceDocument
from app.modules.sales_playbook.factory import get_sales_playbook_generator
from app.modules.sales_playbook.generation_models import (
    GenerationFailureCode,
    GenerationJobPhase,
    GenerationJobStatus,
    SalesPlaybookGenerationJob,
)
from app.modules.sales_playbook.generation_service import (
    GenerationJobIntegrityError,
    generation_job_payload_matches_fingerprint,
    seller_profile_fingerprint,
)
from app.modules.sales_playbook.generic_contracts import (
    ExtractionContractValidationError,
    build_provenance_diagnostics,
)
from app.modules.sales_playbook.generic_provenance import ProvenanceValidationError
from app.modules.sales_playbook.semantic_playbook import PlaybookAssemblyValidationError
from app.modules.sales_playbook.service import SalesPlaybookService
from app.modules.seller_profile.models import SellerProfile
from app.modules.validation.service import ValidationService

logger = logging.getLogger(__name__)


def execute_prospection_run(run_id: str) -> None:
    """RQ entry point: create an isolated DB session per job."""
    db = SessionLocal()
    try:
        from app.modules.demo.service import DemoSearchService

        if not DemoSearchService(db).claim_dispatch(UUID(run_id)):
            return
        asyncio.run(ProspectionService(db).execute_run(UUID(run_id)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_validation_run(run_id: str) -> None:
    """RQ entry point for validating all leads from a prospection run."""
    db = SessionLocal()
    try:
        lead_ids = list(
            db.execute(
                select(RunLead.lead_id).where(
                    RunLead.run_id == UUID(run_id),
                    RunLead.lead_id.is_not(None),
                )
            ).scalars().all()
        )
        asyncio.run(ValidationService(db).run_validation_job(lead_ids))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_prospect_research(research_id: str) -> None:
    """RQ entry point for bounded Tavily prospect research."""
    db = SessionLocal()
    try:
        ProspectResearchService(db).execute_research(UUID(research_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_lead_fit_evaluation(evaluation_id: str) -> None:
    """RQ entry point for one server-loaded, leased Lead Fit evaluation."""
    from app.modules.lead_fit.evaluation_service import LeadFitEvaluationService

    db = SessionLocal()
    try:
        evaluation = LeadFitEvaluationService(db).execute(UUID(evaluation_id))
        if evaluation is None:
            raise LookupError("Lead Fit evaluation not found")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_lead_fit_research(grant_id: str) -> None:
    """RQ entry point for one explicitly authorized bounded research grant."""
    from app.modules.lead_fit.research import LeadFitResearchService

    db = SessionLocal()
    try:
        grant = LeadFitResearchService(db).execute(UUID(grant_id))
        if grant is None:
            raise LookupError("Lead Fit research grant not found")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


_FAILURE_MESSAGES = {
    GenerationFailureCode.PROVIDER_TIMEOUT: "El análisis tardó más de lo esperado y no pudo completarse.",
    GenerationFailureCode.PROVIDER_ERROR: "El servicio de análisis no pudo completar la solicitud.",
    GenerationFailureCode.INVALID_STRUCTURED_OUTPUT: "No pudimos interpretar de forma segura una parte de la documentación.",
    GenerationFailureCode.SOURCE_NORMALIZATION_ERROR: "No pudimos preparar uno de los documentos. Revísalo e inténtalo de nuevo.",
    GenerationFailureCode.EXTRACTION_ERROR: "No pudimos completar el análisis de tu documentación.",
    GenerationFailureCode.EXTRACTION_CONTRACT_ERROR: "No pudimos validar de forma segura una parte del análisis de tu documentación.",
    GenerationFailureCode.PROVENANCE_VALIDATION_ERROR: "No pudimos verificar de forma segura una parte del análisis de tu documentación.",
    GenerationFailureCode.PLAYBOOK_ASSEMBLY_VALIDATION_ERROR: "No pudimos validar de forma segura el borrador ensamblado.",
    GenerationFailureCode.INTERNAL_ERROR: "Ocurrió un error interno al preparar el borrador.",
}

_PROGRESS_PRESENTATION = {
    "plan": (GenerationJobPhase.ANALYZING_DOCUMENTATION, "Analizando documentación"),
    "semantic": (GenerationJobPhase.ANALYZING_DOCUMENTATION, "Identificando políticas"),
    "workflow_start": (GenerationJobPhase.BUILDING_WORKFLOW, "Construyendo proceso comercial"),
    "workflow": (GenerationJobPhase.BUILDING_WORKFLOW, "Construyendo proceso comercial"),
    "source_claims_start": (GenerationJobPhase.IDENTIFYING_POLICIES, "Identificando políticas"),
    "source_claims": (GenerationJobPhase.IDENTIFYING_POLICIES, "Identificando políticas"),
    "validating": (GenerationJobPhase.VALIDATING_DRAFT, "Validando borrador"),
    "analysis": (GenerationJobPhase.ANALYZING_DOCUMENTATION, "Analizando documentación"),
}


def classify_generation_failure(error: Exception) -> GenerationFailureCode:
    from app.modules.sales_playbook.execution_budget import ExecutionBudgetExceeded

    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, ExecutionBudgetExceeded) for item in chain):
        return GenerationFailureCode.EXTRACTION_ERROR
    if any(isinstance(item, PlaybookAssemblyValidationError) for item in chain):
        return GenerationFailureCode.PLAYBOOK_ASSEMBLY_VALIDATION_ERROR
    if any(isinstance(item, ProvenanceValidationError) for item in chain):
        return GenerationFailureCode.PROVENANCE_VALIDATION_ERROR
    if any(isinstance(item, ExtractionContractValidationError) for item in chain):
        return GenerationFailureCode.EXTRACTION_CONTRACT_ERROR
    if any(isinstance(item, httpx.TimeoutException | TimeoutError) for item in chain):
        return GenerationFailureCode.PROVIDER_TIMEOUT
    if any(isinstance(item, AIContractValidationError) for item in chain):
        return GenerationFailureCode.INVALID_STRUCTURED_OUTPUT
    messages = " ".join(str(item).lower() for item in chain)
    if "semantic draft generation is unavailable" in messages:
        return GenerationFailureCode.EXTRACTION_ERROR
    if "normalized source document" in messages or "source document could not be normalized" in messages:
        return GenerationFailureCode.SOURCE_NORMALIZATION_ERROR
    if any(term in messages for term in ("structured output", "finish_reason", "pydantic", "schema is invalid")):
        return GenerationFailureCode.INVALID_STRUCTURED_OUTPUT
    if "provider" in messages or "http" in messages:
        return GenerationFailureCode.PROVIDER_ERROR
    if isinstance(error, ValueError | LookupError):
        return GenerationFailureCode.EXTRACTION_ERROR
    return GenerationFailureCode.INTERNAL_ERROR


def _generation_snapshot_is_current(db, job: SalesPlaybookGenerationJob) -> bool:
    if not generation_job_payload_matches_fingerprint(job):
        return False
    if job.resume_from_generation_job_id is not None:
        from app.modules.sales_playbook.generation_service import (
            RQExecutionState,
            inspect_generation_rq_state,
        )

        source = db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == job.resume_from_generation_job_id,
            SalesPlaybookGenerationJob.workspace_id == job.workspace_id,
        )).scalar_one_or_none()
        if (source is None or source.status != GenerationJobStatus.FAILED
                or not generation_job_payload_matches_fingerprint(source)
                or inspect_generation_rq_state(source.rq_job_id or "") != RQExecutionState.TERMINAL):
            return False
        from app.modules.sales_playbook.models import SalesPlaybook

        if (source.result_playbook_id is not None
                or db.execute(select(SalesPlaybook.id).where(
                    SalesPlaybook.workspace_id == job.workspace_id,
                    SalesPlaybook.idempotency_key == str(source.id),
                )).scalar_one_or_none() is not None):
            return False
        source_metadata = source.progress_metadata or {}
        current_metadata = job.progress_metadata or {}
        if (source_metadata.get("semantic_checkpoint_format")
                != "semantic_operation_checkpoint_v1"
                or source_metadata.get("semantic_checkpoint_source_status") == "blocked"
                or source_metadata.get("semantic_checkpoint_context_fingerprint")
                != current_metadata.get("semantic_checkpoint_context_fingerprint")
                or source_metadata.get("semantic_checkpoint_input_identity")
                != current_metadata.get("semantic_checkpoint_input_identity")
                or source.source_manifest != job.source_manifest
                or source.source_type != job.source_type
                or source.seller_profile_id != job.seller_profile_id
                or source.seller_profile_fingerprint != job.seller_profile_fingerprint):
            return False
        source_payload = {key: value for key, value in (source.request_payload or {}).items()
                          if key != "idempotency_key"}
        current_payload = {key: value for key, value in (job.request_payload or {}).items()
                           if key != "idempotency_key"}
        if source_payload != current_payload:
            return False
    manifest = job.source_manifest or []
    if manifest:
        ids = [UUID(item["document_id"]) for item in manifest]
        records = db.execute(select(SourceDocument).where(
            SourceDocument.workspace_id == job.workspace_id,
            SourceDocument.id.in_(ids),
        )).scalars().all()
        by_id = {record.id: record for record in records}
        if len(by_id) != len(ids):
            return False
        for expected in manifest:
            record = by_id[UUID(expected["document_id"])]
            if (record.normalization_status != "complete"
                    or record.normalized_sha256 != expected["normalized_sha256"]
                    or record.original_sha256 != expected["original_sha256"]):
                return False
    if job.seller_profile_id is None:
        return job.seller_profile_fingerprint is None
    profile_query = select(SellerProfile).where(
        SellerProfile.workspace_id == job.workspace_id,
        SellerProfile.id == job.seller_profile_id,
    )
    profile = db.execute(profile_query).scalar_one_or_none()
    return profile is not None and seller_profile_fingerprint(profile) == job.seller_profile_fingerprint


def _record_generation_progress(job_id: str, phase: str, current_step: int, total_steps: int) -> None:
    if phase not in _PROGRESS_PRESENTATION:
        raise ValueError("unknown generation progress phase")
    mapped_phase, message = _PROGRESS_PRESENTATION[phase]
    db = SessionLocal()
    try:
        job = db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == UUID(job_id),
        ).with_for_update()).scalar_one_or_none()
        if job is None or job.status != GenerationJobStatus.RUNNING:
            raise RuntimeError("generation job is no longer running")
        if (total_steps < job.total_steps or not 0 <= current_step <= total_steps
                or (total_steps > job.total_steps and phase != "plan")):
            raise RuntimeError("generation plan changed outside deterministic pre-execution planning")
        job.phase = mapped_phase
        job.current_step = max(job.current_step, current_step)
        job.total_steps = total_steps
        job.progress_message = message
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _fail_generation_job(job_id: UUID, error: Exception) -> None:
    from app.modules.sales_playbook.execution_budget import ExecutionBudgetExceeded

    code = classify_generation_failure(error)
    db = SessionLocal()
    try:
        job = db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == job_id,
        ).with_for_update()).scalar_one_or_none()
        if job is None or job.status in (GenerationJobStatus.SUCCEEDED, GenerationJobStatus.FAILED):
            return
        job.status = GenerationJobStatus.FAILED
        job.failure_code = code
        budget_error = next(
            (item for item in _exception_chain(error) if isinstance(item, ExecutionBudgetExceeded)),
            None,
        )
        job.failure_message = (
            "El presupuesto de ejecución se agotó; las checkpoints validadas se conservaron."
            if budget_error is not None else _FAILURE_MESSAGES[code]
        )
        metadata = dict(job.progress_metadata or {})
        budget_ledger = metadata.get("execution_budget")
        if isinstance(budget_ledger, dict) and budget_ledger.get("format") == "sales_playbook_execution_budget_v1":
            if budget_ledger.get("state") == "running":
                budget_ledger["state"] = "failed"
            budget_ledger["closed_at_utc"] = datetime.now(UTC).isoformat()
            metadata["execution_budget"] = budget_ledger
            job.progress_metadata = metadata
        if budget_error is not None:
            allowed_diagnostic_keys = {
                "code", "operation_id", "operation_phase", "operation_count", "max_operations",
                "dynamic_operation_count", "max_dynamic_operations", "reserved_attempts",
                "dispatched_attempts", "finalized_attempts", "failed_attempts",
                "remaining_provider_seconds", "required_provider_seconds", "request_timeout_seconds",
                "limits",
            }
            safe_diagnostics = {
                key: value for key, value in budget_error.diagnostics.items()
                if key in allowed_diagnostic_keys and isinstance(value, str | int | float | dict)
            }
            metadata = dict(job.progress_metadata or {})
            metadata["execution_budget_failure"] = safe_diagnostics
            job.progress_metadata = metadata
        provenance_error = next(
            (item for item in _exception_chain(error) if isinstance(item, ProvenanceValidationError)),
            None,
        )
        contract_error = next(
            (item for item in _exception_chain(error) if isinstance(item, ExtractionContractValidationError)),
            None,
        )
        if provenance_error is not None or contract_error is not None:
            metadata = dict(job.progress_metadata or {})
            if provenance_error is not None:
                metadata["extraction_failure_diagnostics"] = build_provenance_diagnostics(
                    provenance_error.diagnostics,
                )
            else:
                metadata["extraction_failure_diagnostics"] = contract_error.diagnostics
            job.progress_metadata = metadata
        structured_validation_error = next(
            (item for item in _exception_chain(error) if isinstance(item, AIContractValidationError)),
            None,
        )
        structured_diagnostics = getattr(structured_validation_error, "diagnostics", None)
        if isinstance(structured_diagnostics, dict):
            metadata = dict(job.progress_metadata or {})
            metadata["structured_output_validation_diagnostics"] = structured_diagnostics
            job.progress_metadata = metadata
        assembly_error = next(
            (item for item in _exception_chain(error) if isinstance(item, PlaybookAssemblyValidationError)),
            None,
        )
        if assembly_error is not None:
            metadata = dict(job.progress_metadata or {})
            metadata["assembly_failure_diagnostics"] = {
                key: value for key, value in assembly_error.diagnostics.items()
                if key in {
                    "phase", "contract", "validation_code", "field", "actual_count", "maximum",
                    "pre_consolidation_count", "post_consolidation_count", "counts_by_finding_class",
                    "duplicate_groups_collapsed", "findings_with_multiple_provenance",
                    "global_unknown_count", "unmapped_workflow_count", "source_conflict_count",
                    "compilation_gap_count",
                }
            }
            metadata["assembly_failure_diagnostics"]["correlation_id"] = str(job.id)
            job.progress_metadata = metadata
        job.finished_at = datetime.now(UTC)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _exception_chain(error: BaseException):
    chain = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def execute_sales_playbook_generation(generation_job_id: str, generator_override=None) -> None:
    """RQ entry point: run one extraction without holding a DB transaction across inference."""
    settings = get_settings()
    if settings.sales_playbook_test_execution:
        from app.workers.test_mode import assert_isolated_sales_playbook_test_worker

        assert_isolated_sales_playbook_test_worker(settings)
    job_id = UUID(generation_job_id)
    db = SessionLocal()
    try:
        job = db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == job_id,
        ).with_for_update()).scalar_one_or_none()
        if job is None or job.status != GenerationJobStatus.QUEUED:
            return
        if not _generation_snapshot_is_current(db, job):
            db.rollback()
            _fail_generation_job(job_id, GenerationJobIntegrityError())
            return
        job.status = GenerationJobStatus.RUNNING
        job.phase = GenerationJobPhase.PREPARING_DOCUMENTS
        job.progress_message = "Preparando documentación"
        job.started_at = datetime.now(UTC)
        db.commit()

        from app.modules.sales_playbook.schemas import SalesPlaybookDraftCreate

        data = SalesPlaybookDraftCreate.model_validate(job.request_payload)
        generator = generator_override or get_sales_playbook_generator()

        def progress(phase: str, current_step: int, total_steps: int) -> None:
            deadline_guard = getattr(generator, "assert_execution_deadline", None)
            if callable(deadline_guard):
                deadline_guard()
            _record_generation_progress(generation_job_id, phase, current_step, total_steps)

        service = SalesPlaybookService(db, generator=generator)
        playbook = service.create_draft(
            workspace_id=job.workspace_id,
            source_type=data.source_type,
            questionnaire=data.questionnaire.model_dump() if data.questionnaire else None,
            pasted_text=data.pasted_text,
            seller_profile_id=job.seller_profile_id,
            idempotency_key=str(job.id),
            source_document_ids=data.source_document_ids,
            progress_callback=progress,
            release_transaction_before_generation=True,
            generation_job_id=job.id,
        )
        deadline_guard = getattr(generator, "assert_execution_deadline", None)
        if callable(deadline_guard):
            deadline_guard()
        job = db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == job_id,
            SalesPlaybookGenerationJob.workspace_id == job.workspace_id,
        ).with_for_update()).scalar_one()
        if job.status is not GenerationJobStatus.RUNNING:
            raise RuntimeError("generation job stopped before successful finalization")
        metadata = dict(job.progress_metadata or {})
        budget_ledger = metadata.get("execution_budget")
        if isinstance(budget_ledger, dict) and budget_ledger.get("format") == "sales_playbook_execution_budget_v1":
            if budget_ledger.get("state") != "running":
                raise RuntimeError("execution budget ledger is not open at job finalization")
            budget_ledger["state"] = "completed"
            budget_ledger["closed_at_utc"] = datetime.now(UTC).isoformat()
            metadata["execution_budget"] = budget_ledger
            job.progress_metadata = metadata
        job.status = GenerationJobStatus.SUCCEEDED
        job.phase = GenerationJobPhase.COMPLETE
        job.current_step = job.total_steps
        job.progress_message = "Borrador listo para revisión"
        job.result_playbook_id = playbook.id
        assembly_metrics = getattr(service.generator, "last_assembly_metrics", None)
        if isinstance(assembly_metrics, dict):
            from app.modules.sales_playbook.service import _safe_assembly_metrics

            safe_metrics = _safe_assembly_metrics(assembly_metrics)
            if safe_metrics is not None:
                metadata = dict(job.progress_metadata or {})
                metadata["assembly_metrics"] = safe_metrics
                job.progress_metadata = metadata
        job.finished_at = datetime.now(UTC)
        db.commit()
    except Exception as error:
        db.rollback()
        try:
            _fail_generation_job(job_id, error)
        except Exception:
            # Preserve worker diagnostics while never storing raw details in the job row.
            raise
        logger.exception("SalesPlaybook generation job failed job_id=%s", generation_job_id)
    finally:
        db.close()
