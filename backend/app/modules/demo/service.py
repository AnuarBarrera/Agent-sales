"""Bounded composition of existing prospecting and reviewed drafting services."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.modules.demo.errors import DemoError
from app.modules.demo.models import DemoDraftReservation, DemoParticipant, DemoSearchReservation
from app.modules.demo.quota import DemoQuotaRepository
from app.modules.lead_fit.contracts import canonical_sha256
from app.modules.leads.models import Lead
from app.modules.prospection.models import ProspectionRun, ProspectionRunStatus

_TERMINAL = {ProspectionRunStatus.READY, ProspectionRunStatus.PARTIAL, ProspectionRunStatus.FAILED}


def require_demo_enabled(settings=None):
    if not (settings or get_settings()).demo_public_mode:
        raise DemoError("demo_unavailable", "La demo no está disponible temporalmente.", 503)


def _key(value: UUID) -> bytes:
    # Reject arbitrary mutable strings even for internal callers.
    return hashlib.sha256(str(UUID(str(value))).encode("ascii")).digest()


def _search_request_digest(payload) -> bytes:
    material = payload.model_dump(mode="json")
    if not payload.enrich_candidates:
        # Preserve the request digest used before this optional flag existed.
        material.pop("enrich_candidates", None)
    return bytes.fromhex(canonical_sha256(material))


class DemoSearchService:
    def __init__(self, db, *, repository=None, create_run=None, enqueue=None, settings=None):
        self.db = db
        self.repo = repository or DemoQuotaRepository(db)
        if create_run is None:
            from app.modules.prospection.service import ProspectionService
            create_run = ProspectionService(db).create_run
        self.create_run = create_run
        self.enqueue = enqueue or self._enqueue
        self.settings = settings or get_settings()

    @staticmethod
    def _enqueue(run_id):
        from app.workers.queue import enqueue_prospection_run
        return enqueue_prospection_run(run_id)

    def create_search(self, *, principal, payload, idempotency_key: UUID):
        require_demo_enabled(self.settings)
        if payload.discovery_strategy != "maps" or not 1 <= payload.candidate_limit <= 3:
            raise DemoError("demo_search_invalid", "Selecciona Maps y entre uno y tres negocios.", 422)
        digest = _search_request_digest(payload)
        key_hash = _key(idempotency_key)
        participant = self.repo.lock_admission(principal)
        existing = self.repo.search_by_key(principal.participant_id, key_hash)
        if existing is not None:
            if existing.request_digest != digest:
                raise DemoError("idempotency_conflict", "Esta solicitud ya fue usada con otros datos.")
            run = self.repo.run(existing.run_id, principal.workspace_id)
            if run is None:
                raise DemoError("demo_run_unavailable", "No se pudo recuperar la búsqueda.")
            return self._deliver(existing.id, run)
        if self.repo.count_searches(participant.id) >= participant.search_limit:
            raise DemoError("demo_search_limit_reached", "Ya utilizaste tus dos búsquedas de demo.", 429)
        if self.repo.count_searches() >= self.settings.demo_global_search_limit:
            raise DemoError("demo_capacity_reached", "La demo alcanzó su capacidad disponible.", 503)
        try:
            run = self.create_run(principal.user_id, principal.workspace_id, payload)
        except ValueError as exc:
            raise DemoError("demo_authority_changed", "Revisa las condiciones aprobadas antes de buscar.") from exc
        reservation = self.repo.add_search(principal, run, key_hash, digest)
        self.db.commit()  # Admission and normal run are durable before Redis sees them.
        return self._deliver(reservation.id, run)

    def _deliver(self, reservation_id, run):
        # Serialize queue delivery after admission has committed. A crashed network
        # acknowledgement remains conservative; worker claim is the final spend guard.
        reservation = self.repo.locked_search(reservation_id)
        if (reservation.enqueued_at is None and reservation.dispatch_started_at is None
                and run.status not in _TERMINAL):
            try:
                self.enqueue(str(run.id))
                reservation.enqueued_at = datetime.now(UTC)
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                error = DemoError("demo_queue_unavailable", "No se pudo iniciar la búsqueda; reintenta la misma solicitud.", 503)
                error.detail["run_id"] = str(run.id)
                raise error from exc
        return run

    def claim_dispatch(self, run_id: UUID) -> bool:
        run = self.db.execute(select(ProspectionRun).where(
            ProspectionRun.id == run_id,
        ).with_for_update()).scalar_one_or_none()
        if run is None:
            return False
        participant = self.db.execute(select(DemoParticipant).where(
            DemoParticipant.workspace_id == run.workspace_id,
        )).scalar_one_or_none()
        if participant is None:
            return True  # Existing internal jobs retain their execution path.
        reservation = self.db.execute(select(DemoSearchReservation).where(
            DemoSearchReservation.participant_id == participant.id,
            DemoSearchReservation.run_id == run.id,
        ).with_for_update()).scalar_one_or_none()
        if reservation is None or reservation.dispatch_started_at is not None or run.status in _TERMINAL:
            return False
        if not self.settings.demo_public_mode:
            run.status = ProspectionRunStatus.FAILED
            run.completed_at = datetime.now(UTC)
            run.error_summary = "Demo dispatch disabled before provider access"
            self.db.commit()
            return False
        reservation.dispatch_started_at = datetime.now(UTC)
        self.db.commit()  # Never undo a spend claim after an external request.
        return True


class DemoDraftService:
    def __init__(self, db, *, repository=None, setter=None, settings=None):
        self.db = db
        self.repo = repository or DemoQuotaRepository(db)
        if setter is None:
            from app.modules.setter.service import SetterService
            setter = SetterService(db)
        self.setter = setter
        self.settings = settings or get_settings()

    def create_or_reuse_draft(self, *, principal, lead_id: UUID, channel: str, idempotency_key: UUID):
        from app.modules.setter.models import SetterExecution
        from app.modules.setter.service import _context_digest, _restrict_channel

        require_demo_enabled(self.settings)
        if channel not in {"email", "phone"}:
            raise DemoError("demo_channel_invalid", "Selecciona correo o pitch de llamada.", 422)
        self.repo.lock_participant(principal)
        lead = self.db.execute(select(Lead).where(
            Lead.id == lead_id, Lead.workspace_id == principal.workspace_id,
        )).scalar_one_or_none()
        if lead is None:
            raise DemoError("demo_lead_not_found", "No se encontró el prospecto.", 404)
        readiness = self.setter.preview_draft_readiness(lead_id, principal.workspace_id)
        if not readiness.draft_ready or channel not in readiness.contact_channels:
            raise DemoError("demo_draft_not_ready", "La evidencia o las condiciones no permiten este borrador.")
        digest = bytes.fromhex(canonical_sha256({"lead_id": str(lead_id), "channel": channel}))
        reservation = self.repo.draft(principal.participant_id, lead_id)
        if reservation is not None:
            if reservation.request_digest != digest:
                raise DemoError("idempotency_conflict", "Este prospecto ya tiene otra intención de borrador.")
            if reservation.execution_id is None:
                raise DemoError("demo_draft_in_progress", "El borrador fue admitido; no se repetirá una ejecución incierta.")
            execution = self.db.execute(select(SetterExecution).where(
                SetterExecution.id == reservation.execution_id,
                SetterExecution.workspace_id == principal.workspace_id,
                SetterExecution.lead_id == lead_id,
            )).scalar_one_or_none()
            context = _restrict_channel(self.setter.preflight(lead_id, principal.workspace_id).context, channel)
            if (execution is None or execution.review_status != "pending_human_review"
                    or execution.decision.get("context_digest") != _context_digest(context)):
                raise DemoError("demo_draft_stale", "El borrador es histórico; revisa la autoridad y sus fuentes.")
            return execution
        reservation = self.repo.add_draft(principal, lead_id, channel, _key(idempotency_key), digest)
        reservation.dispatch_started_at = datetime.now(UTC)
        reservation.status = "dispatched"
        reservation_id = reservation.id
        self.db.commit()
        try:
            execution = self.setter.execute(lead_id, principal.workspace_id, principal.user_id, channel=channel)
            if (execution.review_status != "pending_human_review" or not execution.draft
                    or execution.draft.get("channel") != channel):
                raise DemoError("demo_draft_invalid", "No se produjo un borrador pendiente válido.")
            # The service may have expired ORM rows across its own queries; link the
            # exact persisted reservation and execution in this final transaction.
            current = self.db.execute(select(DemoDraftReservation).where(
                DemoDraftReservation.id == reservation_id,
                DemoDraftReservation.participant_id == principal.participant_id,
            ).with_for_update()).scalar_one()
            current.execution_id = execution.id
            self.db.commit()
            return execution
        except Exception as exc:
            self.db.rollback()  # Durable dispatch claim survives; no automatic retry.
            if isinstance(exc, DemoError):
                raise
            raise DemoError("demo_draft_unavailable", "No se pudo completar el borrador; no se repetirá automáticamente.", 503) from exc
