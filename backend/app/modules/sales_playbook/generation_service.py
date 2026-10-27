from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.sales_playbook.document_models import SourceDocument
from app.modules.sales_playbook.generation_models import (
    GenerationFailureCode,
    GenerationJobPhase,
    GenerationJobStatus,
    SalesPlaybookGenerationJob,
)
from app.modules.sales_playbook.generic_contracts import ExtractionContractValidationError
from app.modules.sales_playbook.models import SalesPlaybook
from app.modules.sales_playbook.schemas import SalesPlaybookDraftCreate
from app.modules.sales_playbook.source_planning import (
    ExtractionSourceSection,
    build_generic_extraction_plan,
    source_sections_from_markdown,
)
from app.modules.seller_profile.models import SellerProfile

GENERATION_CONTRACT_VERSION = "sales-playbook-v3-generic-6.5.5.13"
RELATION_INTERPRETATION_CONTRACT_VERSION = "relation_candidate_ir_v1"
RELATION_PROMPT_REVISION_VERSION = "assertion-led-relation-candidate-v1"
SAFE_ENQUEUE_FAILURE = "No pudimos iniciar el análisis. Inténtalo de nuevo."
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(value: str) -> bool:
    return bool(_SHA256.fullmatch(value))


class GenerationJobIdempotencyConflict(ValueError):
    """An idempotency key was reused for different generation input."""


class GenerationJobIntegrityError(ExtractionContractValidationError):
    """The persisted job payload is inconsistent with its authorized fingerprint."""

    def __init__(self):
        super().__init__({
            "category": "generation_job_integrity",
            "validation_code": "GENERATION_JOB_SNAPSHOT_MISMATCH",
            "contract": GENERATION_CONTRACT_VERSION,
        })


class GenerationResumeRejected(ValueError):
    """Safe explicit-resume admission failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RQExecutionState(StrEnum):
    ACTIVE = "active"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


def inspect_generation_rq_state(rq_job_id: str) -> RQExecutionState:
    """Inspect the job and all execution registries; any uncertainty fails closed."""
    if not rq_job_id:
        return RQExecutionState.UNKNOWN
    try:
        from rq.registry import (
            DeferredJobRegistry,
            FailedJobRegistry,
            FinishedJobRegistry,
            ScheduledJobRegistry,
            StartedJobRegistry,
        )

        from app.workers.queue import get_queue

        queue = get_queue()
        connection = queue.connection
        job = queue.fetch_job(rq_job_id)
        active_membership = rq_job_id in set(queue.get_job_ids())
        for registry_type in (StartedJobRegistry, DeferredJobRegistry, ScheduledJobRegistry):
            active_membership = active_membership or rq_job_id in set(
                registry_type(queue=queue, connection=connection).get_job_ids(),
            )
        terminal_membership = False
        for registry_type in (FailedJobRegistry, FinishedJobRegistry):
            terminal_membership = terminal_membership or rq_job_id in set(
                registry_type(queue=queue, connection=connection).get_job_ids(),
            )
        status = None
        if job is not None:
            raw_status = job.get_status(refresh=True)
            status = getattr(raw_status, "value", raw_status)
        if active_membership or status in {"queued", "started", "deferred", "scheduled"}:
            return RQExecutionState.ACTIVE
        if status in {"failed", "finished", "stopped", "canceled"} or terminal_membership:
            return RQExecutionState.TERMINAL
        return RQExecutionState.UNKNOWN
    except Exception:
        return RQExecutionState.UNKNOWN


def generation_request_fingerprint(
    *,
    workspace_id: UUID,
    source_type: str,
    request_payload: dict[str, Any],
    source_manifest: list[dict[str, Any]],
    seller_profile_id: UUID | None,
    seller_profile_fingerprint: str | None,
    resume_from_generation_job_id: UUID | None = None,
) -> str:
    request = {
        "workspace_id": str(workspace_id),
        "source_type": source_type,
        "request_payload": request_payload,
        "source_manifest": source_manifest,
        "seller_profile_id": str(seller_profile_id) if seller_profile_id else None,
        "seller_profile_fingerprint": seller_profile_fingerprint,
        "contract_version": GENERATION_CONTRACT_VERSION,
        "relation_contract_version": RELATION_INTERPRETATION_CONTRACT_VERSION,
        "relation_prompt_revision": RELATION_PROMPT_REVISION_VERSION,
    }
    if resume_from_generation_job_id is not None:
        request["resume_from_generation_job_id"] = str(resume_from_generation_job_id)
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generation_job_payload_matches_fingerprint(job: SalesPlaybookGenerationJob) -> bool:
    """Fail closed unless the persisted payload still matches its authorized snapshot."""
    try:
        payload = dict(job.request_payload or {})
        if payload.get("idempotency_key") != job.idempotency_key:
            return False
        data = SalesPlaybookDraftCreate.model_validate(payload)
        if data.source_type.value != job.source_type:
            return False

        payload_document_ids = [str(item) for item in data.source_document_ids]
        manifest_document_ids = [str(item["document_id"]) for item in job.source_manifest]
        if payload_document_ids != manifest_document_ids:
            return False
        # A null request profile is valid when the service resolved its current
        # workspace default into the persisted job snapshot. Explicit IDs must match.
        if (data.seller_profile_id is not None
                and data.seller_profile_id != job.seller_profile_id):
            return False

        metadata = job.progress_metadata or {}
        if (metadata.get("contract_version") != GENERATION_CONTRACT_VERSION
                or metadata.get("relation_contract_version")
                != RELATION_INTERPRETATION_CONTRACT_VERSION
                or metadata.get("relation_prompt_revision") != RELATION_PROMPT_REVISION_VERSION):
            return False

        expected_fingerprint = generation_request_fingerprint(
            workspace_id=job.workspace_id,
            source_type=job.source_type,
            request_payload={key: value for key, value in payload.items()
                             if key != "idempotency_key"},
            source_manifest=job.source_manifest,
            seller_profile_id=job.seller_profile_id,
            seller_profile_fingerprint=job.seller_profile_fingerprint,
            resume_from_generation_job_id=getattr(job, "resume_from_generation_job_id", None),
        )
        return expected_fingerprint == job.request_fingerprint
    except (KeyError, TypeError, ValueError):
        return False


def seller_profile_fingerprint(profile: SellerProfile | None) -> str | None:
    if profile is None:
        return None
    fields = (
        "business_name", "description", "offer_summary", "target_customer_summary",
        "location_or_market", "price_context", "primary_goal", "differentiators", "sales_constraints",
    )
    values = {field: getattr(profile, field) for field in fields}
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SalesPlaybookGenerationJobService:
    def __init__(
        self,
        db: Session,
        enqueue: Callable[[str, str, int], str] | None = None,
        rq_state_inspector: Callable[[str], RQExecutionState] | None = None,
    ) -> None:
        self.db = db
        self._uses_rq = enqueue is None
        self._enqueue = enqueue or self._enqueue_rq
        self._rq_state_inspector = rq_state_inspector or inspect_generation_rq_state

    def create_or_reuse(
        self,
        *,
        workspace_id: UUID,
        requested_by: UUID,
        data: SalesPlaybookDraftCreate,
        retry_of_job_id: UUID | None = None,
    ) -> SalesPlaybookGenerationJob:
        payload = data.model_dump(mode="json")
        idempotency_key = data.idempotency_key or str(uuid.uuid4())
        payload["idempotency_key"] = idempotency_key
        manifest, profile, source_records = self._validated_inputs(workspace_id, data)
        profile_id = profile.id if profile else None
        profile_hash = seller_profile_fingerprint(profile)
        fingerprint = generation_request_fingerprint(
            workspace_id=workspace_id,
            source_type=data.source_type.value,
            request_payload={key: value for key, value in payload.items() if key != "idempotency_key"},
            source_manifest=manifest,
            seller_profile_id=profile_id,
            seller_profile_fingerprint=profile_hash,
        )
        total_steps = self._planned_total_steps(data, source_records)

        existing_key = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
            SalesPlaybookGenerationJob.idempotency_key == idempotency_key,
        )).scalar_one_or_none()
        if existing_key is not None:
            if existing_key.request_fingerprint != fingerprint:
                raise GenerationJobIdempotencyConflict("Idempotency key was already used for different input")
            self._ensure_queued(existing_key)
            return existing_key

        active = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
            SalesPlaybookGenerationJob.request_fingerprint == fingerprint,
            SalesPlaybookGenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
        ).order_by(SalesPlaybookGenerationJob.created_at.desc())).scalars().first()
        if active is not None:
            self._ensure_queued(active)
            return active

        job = SalesPlaybookGenerationJob(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            requested_by=requested_by,
            status=GenerationJobStatus.QUEUED,
            phase=GenerationJobPhase.PREPARING_DOCUMENTS,
            current_step=0,
            total_steps=total_steps,
            progress_message="Preparando documentación",
            progress_metadata={
                "contract_version": GENERATION_CONTRACT_VERSION,
                "relation_contract_version": RELATION_INTERPRETATION_CONTRACT_VERSION,
                "relation_prompt_revision": RELATION_PROMPT_REVISION_VERSION,
                **({
                    "semantic_checkpoint_format": "semantic_operation_checkpoint_v1",
                    "semantic_checkpoint_source_status": "pending",
                    "semantic_checkpoint_manifest": {},
                    "semantic_checkpoint_context_fingerprint": fingerprint,
                    "semantic_checkpoint_input_identity": idempotency_key,
                } if data.source_type.value in {"manual", "document"} else {}),
            },
            source_manifest=manifest,
            request_payload=payload,
            source_type=data.source_type.value,
            seller_profile_id=profile_id,
            seller_profile_fingerprint=profile_hash,
            request_fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            retry_of_job_id=retry_of_job_id,
            created_at=datetime.now(UTC),
        )
        job.rq_job_id = f"sales-playbook-generation:{job.id}"
        self.db.add(job)
        try:
            self.db.flush()
            self.db.commit()
        except IntegrityError as integrity_error:
            self.db.rollback()
            existing = self.db.execute(select(SalesPlaybookGenerationJob).where(
                SalesPlaybookGenerationJob.workspace_id == workspace_id,
                SalesPlaybookGenerationJob.request_fingerprint == fingerprint,
                SalesPlaybookGenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
            ).order_by(SalesPlaybookGenerationJob.created_at.desc())).scalars().first()
            if existing is not None:
                self._ensure_queued(existing)
                return existing
            existing = self.db.execute(select(SalesPlaybookGenerationJob).where(
                SalesPlaybookGenerationJob.workspace_id == workspace_id,
                SalesPlaybookGenerationJob.idempotency_key == idempotency_key,
            )).scalar_one_or_none()
            if existing is not None and existing.request_fingerprint == fingerprint:
                self._ensure_queued(existing)
                return existing
            if existing is not None:
                raise GenerationJobIdempotencyConflict(
                    "Idempotency key was already used for different input",
                ) from integrity_error
            raise

        try:
            self._enqueue(str(job.id), job.rq_job_id, job.total_steps)
        except Exception:
            job.status = GenerationJobStatus.FAILED
            job.failure_code = GenerationFailureCode.INTERNAL_ERROR
            job.failure_message = SAFE_ENQUEUE_FAILURE
            job.finished_at = datetime.now(UTC)
            self.db.commit()
        self.db.refresh(job)
        return job

    def get(self, workspace_id: UUID, job_id: UUID) -> SalesPlaybookGenerationJob | None:
        job = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
            SalesPlaybookGenerationJob.id == job_id,
        )).scalar_one_or_none()
        return self._reconcile_rq_state(job)

    def retry_failed(
        self, *, workspace_id: UUID, requested_by: UUID, job_id: UUID,
    ) -> SalesPlaybookGenerationJob:
        previous = self.get(workspace_id, job_id)
        if previous is None:
            raise LookupError("Generation job not found")
        if previous.status != GenerationJobStatus.FAILED:
            raise ValueError("Only failed generation jobs can be retried")
        payload = dict(previous.request_payload)
        payload["idempotency_key"] = str(uuid.uuid4())
        data = SalesPlaybookDraftCreate.model_validate(payload)
        return self.create_or_reuse(
            workspace_id=workspace_id,
            requested_by=requested_by,
            data=data,
            retry_of_job_id=previous.id,
        )

    def resume_failed(
        self,
        *,
        workspace_id: UUID,
        requested_by: UUID,
        source_job_id: UUID,
    ) -> SalesPlaybookGenerationJob:
        """Create a new explicitly selected job; never requeue the source job."""
        source = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == source_job_id,
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
        ).with_for_update()).scalar_one_or_none()
        if source is None:
            raise LookupError("Generation job not found")
        metadata = dict(source.progress_metadata or {})
        if metadata.get("semantic_checkpoint_format") != "semantic_operation_checkpoint_v1":
            raise GenerationResumeRejected("CHECKPOINT_SOURCE_NOT_ENABLED")
        checkpoint_source_status = metadata.get("semantic_checkpoint_source_status")
        if checkpoint_source_status not in {"pending", "eligible"}:
            raise GenerationResumeRejected("CHECKPOINT_SOURCE_BLOCKED_OR_UNKNOWN")
        if source.resume_from_generation_job_id is not None and checkpoint_source_status != "eligible":
            raise GenerationResumeRejected("INCOMPLETE_RESUME_JOB_CANNOT_BE_SOURCE")
        if source.status == GenerationJobStatus.QUEUED:
            raise GenerationResumeRejected("SOURCE_JOB_MAY_STILL_EXECUTE")
        if source.status not in (GenerationJobStatus.FAILED, GenerationJobStatus.RUNNING):
            raise GenerationResumeRejected("SOURCE_JOB_NOT_RESUMABLE")
        if source.result_playbook_id is not None or self.db.execute(select(SalesPlaybook.id).where(
            SalesPlaybook.workspace_id == workspace_id,
            SalesPlaybook.idempotency_key == str(source.id),
        )).scalar_one_or_none() is not None:
            raise GenerationResumeRejected("SOURCE_DRAFT_ALREADY_PERSISTED")
        if self._rq_state_inspector(source.rq_job_id or "") != RQExecutionState.TERMINAL:
            raise GenerationResumeRejected("SOURCE_RQ_STATE_NOT_TERMINAL")
        if not generation_job_payload_matches_fingerprint(source):
            raise GenerationResumeRejected("SOURCE_JOB_FINGERPRINT_INVALID")

        try:
            data = SalesPlaybookDraftCreate.model_validate(source.request_payload)
            manifest, profile, _source_records = self._validated_inputs(workspace_id, data)
        except (TypeError, ValueError, LookupError) as error:
            raise GenerationResumeRejected("SOURCE_INPUT_NO_LONGER_AUTHORIZED") from error
        if (manifest != list(source.source_manifest or [])
                or (profile.id if profile else None) != source.seller_profile_id
                or seller_profile_fingerprint(profile) != source.seller_profile_fingerprint):
            raise GenerationResumeRejected("SOURCE_INPUT_SNAPSHOT_CHANGED")
        context_fingerprint = metadata.get("semantic_checkpoint_context_fingerprint")
        input_identity = metadata.get("semantic_checkpoint_input_identity")
        if (not isinstance(context_fingerprint, str) or not _is_sha256(context_fingerprint)
                or not isinstance(input_identity, str) or not input_identity.strip()):
            raise GenerationResumeRejected("SOURCE_CHECKPOINT_CONTEXT_INVALID")

        if source.status == GenerationJobStatus.RUNNING:
            source.status = GenerationJobStatus.FAILED
            source.failure_code = GenerationFailureCode.INTERNAL_ERROR
            source.failure_message = "El proceso se interrumpió; sus operaciones validadas pueden reanudarse explícitamente."
            source.finished_at = datetime.now(UTC)
            self.db.flush()

        payload = data.model_dump(mode="json")
        new_idempotency_key = f"resume-{uuid.uuid4()}"
        payload["idempotency_key"] = new_idempotency_key
        fingerprint = generation_request_fingerprint(
            workspace_id=workspace_id,
            source_type=source.source_type,
            request_payload={key: value for key, value in payload.items() if key != "idempotency_key"},
            source_manifest=manifest,
            seller_profile_id=source.seller_profile_id,
            seller_profile_fingerprint=source.seller_profile_fingerprint,
            resume_from_generation_job_id=source.id,
        )
        active = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
            SalesPlaybookGenerationJob.request_fingerprint == fingerprint,
            SalesPlaybookGenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
        ).order_by(SalesPlaybookGenerationJob.created_at.desc())).scalars().first()
        if active is not None:
            self.db.commit()
            self._ensure_queued(active)
            return active

        resume_metadata = {
            "contract_version": GENERATION_CONTRACT_VERSION,
            "relation_contract_version": RELATION_INTERPRETATION_CONTRACT_VERSION,
            "relation_prompt_revision": RELATION_PROMPT_REVISION_VERSION,
            "semantic_checkpoint_format": "semantic_operation_checkpoint_v1",
            "semantic_checkpoint_source_status": "pending",
            "semantic_checkpoint_manifest": {},
            "semantic_checkpoint_context_fingerprint": context_fingerprint,
            "semantic_checkpoint_input_identity": input_identity,
            "semantic_checkpoint_resume_source_job_id": str(source.id),
        }
        resumed = SalesPlaybookGenerationJob(
            id=uuid.uuid4(), workspace_id=workspace_id, requested_by=requested_by,
            status=GenerationJobStatus.QUEUED,
            phase=GenerationJobPhase.PREPARING_DOCUMENTS,
            current_step=0, total_steps=source.total_steps,
            progress_message="Preparando reanudación explícita",
            progress_metadata=resume_metadata,
            source_manifest=manifest, request_payload=payload,
            source_type=source.source_type,
            seller_profile_id=source.seller_profile_id,
            seller_profile_fingerprint=source.seller_profile_fingerprint,
            request_fingerprint=fingerprint, idempotency_key=new_idempotency_key,
            retry_of_job_id=None, resume_from_generation_job_id=source.id,
            created_at=datetime.now(UTC),
        )
        resumed.rq_job_id = f"sales-playbook-generation:{resumed.id}"
        self.db.add(resumed)
        try:
            self.db.flush()
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.db.execute(select(SalesPlaybookGenerationJob).where(
                SalesPlaybookGenerationJob.workspace_id == workspace_id,
                SalesPlaybookGenerationJob.request_fingerprint == fingerprint,
                SalesPlaybookGenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
            ).order_by(SalesPlaybookGenerationJob.created_at.desc())).scalars().first()
            if existing is None:
                raise
            return existing
        try:
            self._enqueue(str(resumed.id), resumed.rq_job_id, resumed.total_steps)
        except Exception:
            resumed.status = GenerationJobStatus.FAILED
            resumed.failure_code = GenerationFailureCode.INTERNAL_ERROR
            resumed.failure_message = SAFE_ENQUEUE_FAILURE
            resumed.finished_at = datetime.now(UTC)
            self.db.commit()
        return resumed

    def active(self, workspace_id: UUID) -> SalesPlaybookGenerationJob | None:
        job = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
            SalesPlaybookGenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
        ).order_by(SalesPlaybookGenerationJob.created_at.desc())).scalars().first()
        return self._reconcile_rq_state(job)

    def latest_relevant(self, workspace_id: UUID) -> SalesPlaybookGenerationJob | None:
        """Recover active work or a recent result for the unchanged input context."""
        active = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
            SalesPlaybookGenerationJob.status.in_((
                GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING,
            )),
        ).order_by(
            SalesPlaybookGenerationJob.created_at.desc(),
        )).scalars().first()
        if active is not None:
            return self._reconcile_rq_state(active)
        cutoff = datetime.now(UTC) - timedelta(days=7)
        jobs = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
            SalesPlaybookGenerationJob.status.in_((
                GenerationJobStatus.SUCCEEDED, GenerationJobStatus.FAILED,
            )),
            SalesPlaybookGenerationJob.created_at >= cutoff,
        ).order_by(
            SalesPlaybookGenerationJob.created_at.desc(),
        )).scalars().all()
        for job in jobs:
            if (job.progress_metadata.get("contract_version") != GENERATION_CONTRACT_VERSION
                    or job.progress_metadata.get("relation_contract_version")
                    != RELATION_INTERPRETATION_CONTRACT_VERSION
                    or job.progress_metadata.get("relation_prompt_revision")
                    != RELATION_PROMPT_REVISION_VERSION):
                continue
            if self._snapshot_matches_current_context(job):
                return job
        return None

    def _snapshot_matches_current_context(self, job: SalesPlaybookGenerationJob) -> bool:
        manifest = job.source_manifest or []
        if manifest:
            ids = [UUID(item["document_id"]) for item in manifest]
            records = self.db.execute(select(SourceDocument).where(
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
            manifest_hashes = {
                (item["original_sha256"], item["normalized_sha256"]) for item in manifest
            }
            newer_documents = self.db.execute(select(SourceDocument).where(
                SourceDocument.workspace_id == job.workspace_id,
                SourceDocument.created_at > job.created_at,
            )).scalars().all()
            if any((record.original_sha256, record.normalized_sha256) not in manifest_hashes
                   for record in newer_documents):
                return False
        profile_query = select(SellerProfile).where(SellerProfile.workspace_id == job.workspace_id)
        if job.seller_profile_id is not None:
            profile_query = profile_query.where(SellerProfile.id == job.seller_profile_id)
        profile = self.db.execute(profile_query).scalar_one_or_none()
        return (
            (profile.id if profile else None) == job.seller_profile_id
            and seller_profile_fingerprint(profile) == job.seller_profile_fingerprint
        )

    def _reconcile_rq_state(
        self, job: SalesPlaybookGenerationJob | None,
    ) -> SalesPlaybookGenerationJob | None:
        if (not self._uses_rq or job is None
                or job.status not in (GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)):
            return job
        try:
            rq_state = self._rq_state_inspector(job.rq_job_id or "")
        except Exception:
            return job
        if rq_state is not RQExecutionState.TERMINAL:
            return job
        locked_job = self.db.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == job.id,
            SalesPlaybookGenerationJob.workspace_id == job.workspace_id,
        ).with_for_update()).scalar_one_or_none()
        if locked_job is None or locked_job.status not in (
            GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING,
        ):
            return locked_job
        locked_job.status = GenerationJobStatus.FAILED
        locked_job.failure_code = GenerationFailureCode.INTERNAL_ERROR
        locked_job.failure_message = (
            "El worker terminó antes de cerrar el job; los intentos sin resultado final pueden ser inciertos."
        )
        metadata = dict(locked_job.progress_metadata or {})
        ledger = metadata.get("execution_budget")
        if isinstance(ledger, dict) and ledger.get("format") == "sales_playbook_execution_budget_v1":
            attempts = ledger.get("attempts") if isinstance(ledger.get("attempts"), dict) else {}
            unfinalized = sum(
                item.get("state") in {"reserved", "dispatch_started"}
                for item in attempts.values() if isinstance(item, dict)
            )
            ledger["state"] = "rq_terminated_before_application_close"
            ledger["rq_terminal_observed_at_utc"] = datetime.now(UTC).isoformat()
            ledger["unfinalized_attempts"] = unfinalized
            metadata["execution_budget"] = ledger
            metadata["execution_budget_failure"] = {
                "code": "RQ_TERMINATED_BEFORE_CLEAN_CLOSE",
                "rq_state": "terminal",
                "reserved_attempts": ledger.get("reserved_attempts", 0),
                "dispatched_attempts": ledger.get("dispatched_attempts", 0),
                "finalized_attempts": ledger.get("finalized_attempts", 0),
                "unfinalized_attempts": unfinalized,
                "limits": ledger.get("policy", {}),
            }
            locked_job.progress_metadata = metadata
        locked_job.finished_at = datetime.now(UTC)
        self.db.commit()
        return locked_job

    def _validated_inputs(
        self, workspace_id: UUID, data: SalesPlaybookDraftCreate,
    ) -> tuple[list[dict[str, Any]], SellerProfile | None, list[SourceDocument]]:
        manifest: list[dict[str, Any]] = []
        ordered_records: list[SourceDocument] = []
        if data.source_document_ids:
            records = self.db.execute(select(SourceDocument).where(
                SourceDocument.workspace_id == workspace_id,
                SourceDocument.id.in_(data.source_document_ids),
            )).scalars().all()
            by_id = {record.id: record for record in records}
            if len(by_id) != len(data.source_document_ids):
                raise ValueError("Source document not found in this workspace")
            storage = None
            for document_id in data.source_document_ids:
                record = by_id[document_id]
                if record.normalization_status != "complete":
                    raise ValueError("Source document could not be normalized")
                if not record.sections or not record.original_sha256 or not record.normalized_sha256:
                    raise ValueError("Source document is unavailable for generation")
                try:
                    if storage is None:
                        from app.integrations.storage import StorageProvider

                        storage = StorageProvider()
                    normalized_content = storage.download(record.normalized_object_key)
                except Exception as exc:
                    raise ValueError("Source document is unavailable for generation") from exc
                if hashlib.sha256(normalized_content).hexdigest() != record.normalized_sha256:
                    raise ValueError("Source document integrity validation failed")
                manifest.append({
                    "document_id": str(record.id),
                    "filename": record.original_filename,
                    "normalized_sha256": record.normalized_sha256,
                    "original_sha256": record.original_sha256,
                })
                ordered_records.append(record)

        query = select(SellerProfile).where(SellerProfile.workspace_id == workspace_id)
        if data.seller_profile_id:
            query = query.where(SellerProfile.id == data.seller_profile_id)
        profile = self.db.execute(query).scalar_one_or_none()
        if data.seller_profile_id and profile is None:
            raise ValueError("Seller profile not found")
        return manifest, profile, ordered_records

    def _ensure_queued(self, job: SalesPlaybookGenerationJob) -> None:
        if job.status != GenerationJobStatus.QUEUED:
            return
        try:
            self._enqueue(str(job.id), job.rq_job_id, job.total_steps)
        except Exception:
            job.status = GenerationJobStatus.FAILED
            job.failure_code = GenerationFailureCode.INTERNAL_ERROR
            job.failure_message = SAFE_ENQUEUE_FAILURE
            job.finished_at = datetime.now(UTC)
            self.db.commit()

    @staticmethod
    def _planned_total_steps(data: SalesPlaybookDraftCreate, records: list[SourceDocument]) -> int:
        if data.source_type.value == "questionnaire":
            return 1
        if records:
            sections: list[ExtractionSourceSection] = []
            for record in records:
                for section in record.sections:
                    sections.append(ExtractionSourceSection(
                        document_id=str(record.id),
                        section_id=f"doc:{record.id}:{section['id']}",
                        heading_path=tuple(section["heading_path"]),
                        start_line=section["start_line"],
                        end_line=section["end_line"],
                        order=len(sections),
                        text=section["text"],
                    ))
        else:
            sections = list(source_sections_from_markdown(data.pasted_text or ""))
        units = build_generic_extraction_plan(sections)
        from app.modules.sales_playbook.generic_relations import GenericWorkflowRelationExtractor

        return 2 * len(units) + GenericWorkflowRelationExtractor.expected_operation_count(len(units))

    @staticmethod
    def _enqueue_rq(job_id: str, rq_job_id: str, total_steps: int) -> str:
        from app.workers.queue import enqueue_sales_playbook_generation

        return enqueue_sales_playbook_generation(job_id, rq_job_id, total_steps)
