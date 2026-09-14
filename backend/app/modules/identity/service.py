import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.identity.models import (
    Invitation,
    Membership,
    MembershipStatus,
    User,
    UserRole,
    Workspace,
)
from app.modules.identity.models import (
    Session as SessionModel,
)
from app.modules.identity.schemas import (
    InvitationCreate,
    LoginRequest,
    MembershipUpdate,
    UserCreate,
    WorkspaceCreate,
)


class IdentityService:
    def __init__(self, db: Session):
        self.db = db

    # User methods
    def create_user(self, user_data: UserCreate) -> User:
        user = User(
            email=user_data.email,
            full_name=user_data.full_name,
            hashed_password=hash_password(user_data.password),
        )
        self.db.add(user)
        self.db.flush()
        return user

    def get_user_by_email(self, email: str) -> User | None:
        return self.db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    def get_user_by_id(self, user_id: UUID) -> User | None:
        return self.db.get(User, user_id)

    def authenticate_user(self, login_data: LoginRequest) -> User | None:
        user = self.get_user_by_email(login_data.email)
        if not user or not user.is_active:
            return None
        if not verify_password(login_data.password, user.hashed_password):
            return None
        return user

    def create_tokens(self, user: User, workspace_id: UUID, role: UserRole) -> tuple[str, str]:
        access_token = create_access_token(
            data={"sub": str(user.id), "workspace_id": str(workspace_id), "role": role.value}
        )
        refresh_token = create_refresh_token(
            data={"sub": str(user.id), "workspace_id": str(workspace_id), "role": role.value}
        )
        return access_token, refresh_token

    def store_refresh_token(self, user_id: UUID, refresh_token: str, user_agent: str | None = None, ip_address: str | None = None) -> SessionModel:
        from jose import jwt

        from app.core.config import get_settings
        settings = get_settings()

        # Decode to get expiry
        payload = jwt.decode(refresh_token, settings.secret_key, algorithms=[settings.algorithm])
        expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)

        # Hash the refresh token for storage
        import hashlib
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

        session = SessionModel(
            user_id=user_id,
            refresh_token_hash=token_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
        )
        self.db.add(session)
        self.db.flush()
        return session

    def verify_refresh_token(self, refresh_token: str) -> SessionModel | None:
        import hashlib
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        session = self.db.execute(
            select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
        ).scalar_one_or_none()

        if not session:
            return None
        if session.revoked_at is not None:
            return None
        if datetime.now(UTC) > session.expires_at:
            return None
        return session

    def revoke_session(self, session: SessionModel) -> None:
        session.revoked_at = datetime.now(UTC)

    def rotate_refresh_token(self, old_refresh_token: str, user_agent: str | None = None, ip_address: str | None = None) -> tuple[str, str] | None:
        session = self.verify_refresh_token(old_refresh_token)
        if not session:
            return None

        payload = decode_token(old_refresh_token)
        if not payload or payload.get("type") != "refresh":
            return None

        user = self.get_user_by_id(UUID(payload["sub"]))
        if not user or not user.is_active:
            return None

        workspace_id = UUID(payload["workspace_id"])
        role = UserRole(payload["role"])
        # Revoke old session
        self.revoke_session(session)

        # Create new tokens
        access_token, new_refresh_token = self.create_tokens(user, workspace_id, role)
        self.store_refresh_token(user.id, new_refresh_token, user_agent, ip_address)

        return access_token, new_refresh_token

    # Workspace methods
    def create_workspace(self, workspace_data: WorkspaceCreate, owner: User) -> Workspace:
        workspace = Workspace(name=workspace_data.name, slug=workspace_data.slug)
        self.db.add(workspace)
        self.db.flush()

        # Create admin membership for owner
        membership = Membership(
            user_id=owner.id,
            workspace_id=workspace.id,
            role=UserRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
        self.db.add(membership)
        self.db.flush()

        # Every workspace starts with the same auditable commercial funnel.
        from app.modules.pipeline.service import PipelineService
        PipelineService(self.db).get_default_stages(workspace.id)

        return workspace

    def get_workspace_by_id(self, workspace_id: UUID) -> Workspace | None:
        return self.db.get(Workspace, workspace_id)

    def get_workspace_by_slug(self, slug: str) -> Workspace | None:
        return self.db.execute(select(Workspace).where(Workspace.slug == slug)).scalar_one_or_none()

    # Membership methods
    def get_membership(self, user_id: UUID, workspace_id: UUID) -> Membership | None:
        return self.db.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def get_active_membership(self, user_id: UUID, workspace_id: UUID) -> Membership | None:
        return self.db.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        ).scalar_one_or_none()

    def get_memberships_for_user(self, user_id: UUID) -> list[Membership]:
        return list(self.db.execute(
            select(Membership).where(Membership.user_id == user_id)
        ).scalars().all())

    def get_members_for_workspace(self, workspace_id: UUID) -> list[Membership]:
        return list(self.db.execute(
            select(Membership).where(Membership.workspace_id == workspace_id)
        ).scalars().all())

    def update_membership(
        self, user_id: UUID, workspace_id: UUID, update: MembershipUpdate
    ) -> Membership:
        membership = self.get_membership(user_id, workspace_id)
        if not membership:
            raise LookupError("Membership not found")

        new_role = update.role or membership.role
        new_status = update.status or membership.status
        is_last_admin_change = (
            membership.role == UserRole.ADMIN
            and membership.status == MembershipStatus.ACTIVE
            and (new_role != UserRole.ADMIN or new_status != MembershipStatus.ACTIVE)
        )
        if is_last_admin_change:
            active_admins = self.db.execute(
                select(func.count(Membership.id)).where(
                    Membership.workspace_id == workspace_id,
                    Membership.role == UserRole.ADMIN,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            ).scalar_one()
            if active_admins <= 1:
                raise ValueError("The last active admin cannot be changed")

        membership.role = new_role
        membership.status = new_status
        self.db.flush()
        return membership

    # Invitation methods
    def create_invitation(self, workspace_id: UUID, invitation_data: InvitationCreate, invited_by_id: UUID) -> Invitation:
        now = datetime.now(UTC)
        normalized_email = str(invitation_data.email).lower()
        existing = self.db.execute(
            select(Invitation).where(
                Invitation.workspace_id == workspace_id,
                func.lower(Invitation.email) == normalized_email,
                Invitation.accepted_at.is_(None),
                Invitation.superseded_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing and existing.accepted_at is None and existing.expires_at > now:
            raise ValueError("A pending invitation already exists for this email")

        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(days=7)

        if existing:
            existing.superseded_at = now
            self.db.flush()

        invitation = Invitation(
            workspace_id=workspace_id,
            email=str(invitation_data.email).lower(),
            role=UserRole(invitation_data.role),
            invited_by_id=invited_by_id,
            token=token,
            expires_at=expires_at,
        )
        self.db.add(invitation)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("A pending invitation already exists for this email") from exc
        return invitation

    def get_invitation_by_token(self, token: str) -> Invitation | None:
        return self.db.execute(select(Invitation).where(Invitation.token == token)).scalar_one_or_none()

    def accept_invitation(self, invitation: Invitation, password: str, full_name: str) -> User:
        # Check if user already exists
        user = self.get_user_by_email(invitation.email)
        if user:
            # Update password if needed
            user.hashed_password = hash_password(password)
            user.full_name = full_name
            user.is_active = True
        else:
            # Create new user
            user = self.create_user(
                UserCreate(email=invitation.email, full_name=full_name, password=password)
            )

        membership = self.get_membership(user.id, invitation.workspace_id)
        if membership:
            membership.role = invitation.role
            membership.status = MembershipStatus.ACTIVE
        else:
            membership = Membership(
                user_id=user.id,
                workspace_id=invitation.workspace_id,
                role=invitation.role,
                status=MembershipStatus.ACTIVE,
            )
            self.db.add(membership)

        # Mark invitation as accepted
        invitation.accepted_at = datetime.now(UTC)

        self.db.flush()
        return user
