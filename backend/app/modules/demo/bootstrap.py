"""Idempotently provision one private, unapproved example workspace per email."""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.demo.errors import DemoError
from app.modules.demo.models import DemoParticipant
from app.modules.demo.otp import VerifiedDemoEmail, email_key
from app.modules.demo.templates import DEMO_TEMPLATE_VERSION, demo_template
from app.modules.identity.models import User
from app.modules.identity.schemas import UserCreate, WorkspaceCreate
from app.modules.identity.service import IdentityService
from app.modules.sales_playbook.commercial_service import CommercialContextService
from app.modules.seller_profile.schemas import SellerProfileUpdate
from app.modules.seller_profile.service import SellerProfileService


class SqlAlchemyDemoBootstrapRepository:
    """DB adapter; advisory locking serializes creation even before a row exists."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def lock_email(self, canonical: str) -> None:
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:email_key, 0))"),
            {"email_key": canonical},
        )

    def get_participant(self, canonical: str) -> DemoParticipant | None:
        return self.db.execute(
            select(DemoParticipant).where(DemoParticipant.email_key == canonical)
        ).scalar_one_or_none()

    def find_users(self, canonical: str) -> list[User]:
        rows = self.db.execute(
            select(User).where(func.lower(User.email) == canonical.lower())
        ).scalars().all()
        return [user for user in rows if user.email.casefold() == canonical]

    def get_user_by_id(self, user_id):
        return self.db.get(User, user_id)

    def add_participant(self, participant: DemoParticipant) -> None:
        self.db.add(participant)
        self.db.flush()


class DemoBootstrapService:
    def __init__(self, repository, identity=None, profiles=None, contexts=None) -> None:
        self.repository = repository
        self.identity = identity
        self.profiles = profiles
        self.contexts = contexts

    @classmethod
    def for_session(cls, db: Session) -> DemoBootstrapService:
        return cls(
            SqlAlchemyDemoBootstrapRepository(db),
            IdentityService(db),
            SellerProfileService(db),
            CommercialContextService(db),
        )

    def ensure_participant(self, verified: str | VerifiedDemoEmail) -> DemoParticipant:
        raw_email = verified.email_key if isinstance(verified, VerifiedDemoEmail) else verified
        try:
            canonical = email_key(raw_email)
        except (TypeError, ValueError) as exc:
            raise DemoError("otp_invalid_or_expired", "El código no es válido o ha caducado.", 400) from exc
        self.repository.lock_email(canonical)
        participant = self.repository.get_participant(canonical)
        if participant is not None:
            user = self.repository.get_user_by_id(participant.user_id)
            if user is None or not user.is_active:
                raise DemoError("otp_invalid_or_expired", "El código no es válido o ha caducado.", 400)
            return participant

        users = self.repository.find_users(canonical)
        if len(users) > 1 or (users and not users[0].is_active):
            raise DemoError("otp_invalid_or_expired", "El código no es válido o ha caducado.", 400)
        identity, profiles, contexts = self._services()
        if users:
            user = users[0]
        else:
            user = identity.create_user(UserCreate(
                email=canonical,
                full_name="Participante de demo",
                password=secrets.token_urlsafe(48),
            ))
        slug = "demo-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        workspace = identity.create_workspace(
            WorkspaceCreate(name="Demo Tu Web MX", slug=slug), user,
        )
        profile, _created = profiles.upsert(
            workspace.id,
            SellerProfileUpdate(
                business_name="Tu Web MX (ejemplo)",
                description="Ejemplo editable de negocio vendedor para la demo.",
                offer_summary="Diseño y desarrollo de sitios web para negocios locales.",
                location_or_market="Ciudad de México",
            ),
        )
        contexts.create_draft(
            workspace_id=workspace.id,
            content=demo_template(),
            pasted_text=None,
            source_document_ids=None,
            assisted=False,
        )
        participant = DemoParticipant(
            email_key=canonical,
            user_id=user.id,
            workspace_id=workspace.id,
            template_version=DEMO_TEMPLATE_VERSION,
            search_limit=2,
        )
        self.repository.add_participant(participant)
        return participant

    def _services(self):
        if self.identity is not None and self.profiles is not None and self.contexts is not None:
            return self.identity, self.profiles, self.contexts
        raise RuntimeError("DemoBootstrapService requires a Session or all service adapters")
