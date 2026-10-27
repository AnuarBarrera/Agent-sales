"""Review exact persisted drafts without generation, contact or provider calls."""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.modules.demo.errors import DemoError
from app.modules.demo.models import DemoDraftApproval, DemoDraftReservation
from app.modules.demo.quota import DemoQuotaRepository
from app.modules.demo.schemas import DemoDraftReviewRead
from app.modules.demo.service import require_demo_enabled
from app.modules.lead_fit.contracts import canonical_sha256
from app.modules.leads.models import Lead
from app.modules.setter.context import SetterPreconditionError
from app.modules.setter.models import SetterExecution
from app.modules.setter.service import SetterService, _context_digest, _restrict_channel


class DemoDraftReviewService:
    def __init__(self, db, *, setter=None, repository=None):
        self.db = db
        self.setter = setter or SetterService(db)
        self.repo = repository or DemoQuotaRepository(db)

    def preview(self, *, principal, lead_id, execution_id):
        execution = self.db.execute(select(SetterExecution).where(
            SetterExecution.id == execution_id,
            SetterExecution.workspace_id == principal.workspace_id,
            SetterExecution.lead_id == lead_id,
        )).scalar_one_or_none()
        reservation = self.db.execute(select(DemoDraftReservation).where(
            DemoDraftReservation.participant_id == principal.participant_id,
            DemoDraftReservation.lead_id == lead_id,
            DemoDraftReservation.execution_id == execution_id,
        )).scalar_one_or_none()
        if execution is None or reservation is None:
            raise DemoError("demo_draft_not_found", "No se encontró el borrador de esta demo.", 404)
        digest = canonical_sha256({"draft": execution.draft, "decision": execution.decision,
                                   "validation": execution.validation_result})
        approval = self.db.execute(select(DemoDraftApproval).where(
            DemoDraftApproval.execution_id == execution_id,
            DemoDraftApproval.participant_id == principal.participant_id,
        )).scalar_one_or_none()
        lead = self.db.execute(select(Lead).where(
            Lead.id == lead_id, Lead.workspace_id == principal.workspace_id,
        )).scalar_one_or_none()
        # A completed manual contact retains its exact approval as historical
        # evidence, without authorizing reuse as a new first-contact message.
        if (approval is not None and approval.draft_digest == digest and lead is not None
                and (lead.contacted or lead.first_contacted_at is not None)):
            return DemoDraftReviewRead(execution_id=execution_id, draft_digest=digest,
                status="contact_recorded", approved_at=approval.approved_at)
        reason = self._blocker(principal, lead_id, execution)
        if approval is not None and approval.draft_digest != digest:
            reason = "El borrador cambió desde su aprobación. Revisa la versión vigente."
        return DemoDraftReviewRead(
            execution_id=execution_id, draft_digest=digest,
            status="blocked" if reason else "approved_for_manual_use" if approval else "pending_human_review",
            reason=reason, approved_at=approval.approved_at if approval else None,
        )

    def _blocker(self, principal, lead_id, execution):
        if not execution.draft or not execution.draft.get("message"):
            return "Esta ejecución no contiene un borrador para aprobar."
        validation = execution.validation_result or {}
        if validation.get("status") not in {"passed", "review_required"} or any(
            finding.get("severity") == "error" for finding in validation.get("findings", [])
        ):
            return "El borrador tiene errores de validación que impiden aprobarlo."
        lead = self.db.execute(select(Lead).where(
            Lead.id == lead_id, Lead.workspace_id == principal.workspace_id,
        )).scalar_one_or_none()
        if lead is None or lead.contacted or lead.first_contacted_at is not None:
            return "Este primer contacto ya no está disponible para el prospecto."
        try:
            context = _restrict_channel(self.setter.preflight(lead_id, principal.workspace_id).context,
                                        execution.draft.get("channel"))
            if execution.decision.get("context_digest") != _context_digest(context):
                return "El contexto o la evidencia cambiaron. Este borrador necesita actualizarse."
        except (SetterPreconditionError, ValueError, TypeError):
            return "No se pudo confirmar la vigencia del contexto y la evidencia del borrador."
        return None

    def approve(self, *, principal, lead_id, payload):
        require_demo_enabled()
        # Same participant lock as draft generation: repeated/concurrent approval
        # reads the same durable decision instead of creating a second event.
        self.repo.lock_participant(principal)
        review = self.preview(principal=principal, lead_id=lead_id, execution_id=payload.execution_id)
        if review.draft_digest != payload.draft_digest:
            raise DemoError("demo_draft_changed", "El borrador cambió. Vuelve a revisarlo antes de aprobar.")
        if review.status == "contact_recorded":
            raise DemoError("demo_contact_already_recorded", "El primer contacto ya fue registrado.")
        if review.status == "blocked":
            raise DemoError("demo_draft_approval_blocked", review.reason)
        if review.status == "approved_for_manual_use":
            return review
        now = datetime.now(UTC)
        self.db.add(DemoDraftApproval(
            id=uuid4(), participant_id=principal.participant_id, execution_id=payload.execution_id,
            actor_id=principal.user_id, draft_digest=review.draft_digest, approved_at=now,
        ))
        self.db.commit()
        return review.model_copy(update={"status": "approved_for_manual_use", "approved_at": now})
