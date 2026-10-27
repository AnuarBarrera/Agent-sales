"""SQL persistence and lock order for durable demo admissions."""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from app.modules.demo.errors import DemoError
from app.modules.demo.models import DemoDraftReservation, DemoParticipant, DemoSearchReservation
from app.modules.prospection.models import ProspectionRun

ADMISSION_LOCK = 0x545743524D44454D


class DemoQuotaRepository:
    def __init__(self, db):
        self.db = db

    def lock_participant(self, principal):
        participant = self.db.execute(select(DemoParticipant).where(
            DemoParticipant.id == principal.participant_id,
            DemoParticipant.user_id == principal.user_id,
            DemoParticipant.workspace_id == principal.workspace_id,
        ).with_for_update()).scalar_one_or_none()
        if participant is None:
            raise DemoError("demo_owner_required", "No tienes acceso a esta demo.", 403)
        return participant

    def lock_admission(self, principal):
        self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMISSION_LOCK})
        return self.lock_participant(principal)

    def count_searches(self, participant_id: UUID | None = None) -> int:
        statement = select(func.count()).select_from(DemoSearchReservation)
        if participant_id is not None:
            statement = statement.where(DemoSearchReservation.participant_id == participant_id)
        return self.db.execute(statement).scalar_one()

    def search_by_key(self, participant_id, key_hash):
        return self.db.execute(select(DemoSearchReservation).where(
            DemoSearchReservation.participant_id == participant_id,
            DemoSearchReservation.key_hash == key_hash,
        )).scalar_one_or_none()

    def add_search(self, principal, run, key_hash, digest):
        reservation = DemoSearchReservation(id=uuid4(), participant_id=principal.participant_id,
            run_id=run.id, key_hash=key_hash, request_digest=digest)
        self.db.add(reservation)
        self.db.flush()
        return reservation

    def locked_search(self, reservation_id):
        return self.db.execute(select(DemoSearchReservation).where(
            DemoSearchReservation.id == reservation_id,
        ).with_for_update().execution_options(populate_existing=True)).scalar_one()

    def run(self, run_id, workspace_id):
        return self.db.execute(select(ProspectionRun).where(
            ProspectionRun.id == run_id, ProspectionRun.workspace_id == workspace_id,
        )).scalar_one_or_none()

    def draft(self, participant_id, lead_id):
        return self.db.execute(select(DemoDraftReservation).where(
            DemoDraftReservation.participant_id == participant_id,
            DemoDraftReservation.lead_id == lead_id,
        ).with_for_update()).scalar_one_or_none()

    def add_draft(self, principal, lead_id, channel, key_hash, digest):
        reservation = DemoDraftReservation(id=uuid4(), participant_id=principal.participant_id,
            lead_id=lead_id, channel=channel, key_hash=key_hash, request_digest=digest,
            status="reserved")
        self.db.add(reservation)
        self.db.flush()
        return reservation
