import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import generate_csrf_token
from app.db.session import get_db
from app.modules.identity.models import MembershipStatus, UserRole
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    CurrentUserDemo,
    CurrentUserResponse,
    InvitationCreate,
    InvitationRead,
    LoginRequest,
    MembershipRead,
    MembershipUpdate,
    RefreshRequest,
    TokenResponse,
    UserRead,
    WorkspaceRead,
)
from app.modules.identity.service import IdentityService

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])

settings = get_settings()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(None),
    access_token_cookie: str | None = Cookie(None, alias="access_token"),
) -> tuple[UUID, UUID, UserRole]:
    """Get current user from access token."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif access_token_cookie:
        token = access_token_cookie

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    from app.core.security import decode_token
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user_id = UUID(payload["sub"])
    workspace_id = UUID(payload["workspace_id"])
    # Verify user still exists and is active
    service = IdentityService(db)
    user = service.get_user_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    membership = service.get_active_membership(user_id, workspace_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active membership in this workspace",
        )

    # DB-derived demo ownership and the route allowlist apply independently of
    # public-demo mode so disabling OTP cannot restore unrestricted admin access.
    from app.modules.demo.access import enforce_demo_access, load_demo_principal

    principal = load_demo_principal(db, user_id, workspace_id)
    request.state.demo_principal = principal
    enforce_demo_access(request, principal)

    return user_id, workspace_id, membership.role


def get_current_user_id(
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user),
) -> UUID:
    """Return only the authenticated user's ID for resource endpoints."""
    return current_user[0]


def get_current_workspace_id(
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user),
) -> UUID:
    """Return the workspace from the signed token, never from the request body."""
    return current_user[1]


def require_role(allowed_roles: list[UserRole]) -> Callable[
    [tuple[UUID, UUID, UserRole]], tuple[UUID, UUID, UserRole]
]:
    def role_checker(
        current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user)
    ) -> tuple[UUID, UUID, UserRole]:
        _, _, role = current_user
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_checker


def get_csrf_token(
    response: Response,
    session_id: str | None = Cookie(None),
) -> str:
    """Get or generate CSRF token."""
    if not session_id:
        session_id = secrets.token_urlsafe(16)
        response.set_cookie(
            "session_id",
            session_id,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,  # type: ignore[arg-type]
            max_age=settings.csrf_token_expire_minutes * 60,
        )
    token = generate_csrf_token(session_id)
    return token


@auth_router.get("/csrf")
def csrf(response: Response, session_id: str | None = Cookie(None)) -> dict[str, str]:
    """Issue a CSRF token for browser clients using cookie authentication."""
    token = get_csrf_token(response, session_id)
    return {"csrf_token": token}


@auth_router.post("/login", response_model=TokenResponse)
def login(
    login_data: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
    x_csrf_token: str | None = Header(None),
) -> TokenResponse:
    """Login user and return tokens."""
    if settings.demo_public_mode:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password login is disabled while public demo mode is enabled",
        )
    # For now, skip CSRF on login as it's the first request

    service = IdentityService(db)
    user = service.authenticate_user(login_data)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Get user's first active workspace membership
    memberships = service.get_memberships_for_user(user.id)
    active_membership = next((m for m in memberships if m.status == MembershipStatus.ACTIVE), None)
    if not active_membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active workspace membership",
        )

    # Create tokens
    access_token, refresh_token = service.create_tokens(
        user, active_membership.workspace_id, active_membership.role
    )

    # Store refresh token
    service.store_refresh_token(user.id, refresh_token)

    # Set cookies
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
        csrf_token=csrf_token,
    )


@auth_router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    refresh_data: RefreshRequest | None = None,
    db: Session = Depends(get_db),
    refresh_token_cookie: str | None = Cookie(None, alias="refresh_token"),
    user_agent: str | None = Header(None),
    x_forwarded_for: str | None = Header(None),
) -> TokenResponse:
    """Refresh access token using refresh token."""
    service = IdentityService(db)
    refresh_token = refresh_data.refresh_token if refresh_data else refresh_token_cookie
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")
    result = service.rotate_refresh_token(refresh_token, user_agent, x_forwarded_for)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    access_token, new_refresh_token = result

    # Set new cookies
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        "refresh_token",
        new_refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )

    # Get user for response
    from app.core.security import decode_token

    payload = decode_token(access_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    user = service.get_user_by_id(UUID(payload["sub"]))

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        user=UserRead.model_validate(user),
    )


@auth_router.post("/logout")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: str | None = Cookie(None),
) -> dict[str, str]:
    """Logout user and revoke session."""
    if refresh_token:
        service = IdentityService(db)
        session = service.verify_refresh_token(refresh_token)
        if session:
            service.revoke_session(session)

    # Clear cookies
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    response.delete_cookie("session_id")

    return {"message": "Logged out successfully"}


@auth_router.get("/me", response_model=CurrentUserResponse)
def get_current_user_info(
    request: Request,
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserResponse:
    """Get current user info with workspace and membership."""
    user_id, workspace_id, role = current_user
    service = IdentityService(db)

    user = service.get_user_by_id(user_id)
    workspace = service.get_workspace_by_id(workspace_id)
    membership = service.get_active_membership(user_id, workspace_id)

    principal = getattr(request.state, "demo_principal", None)
    demo = None
    if principal is not None and principal.user_id == user_id and principal.workspace_id == workspace_id:
        demo = CurrentUserDemo(enabled=True, workspace_id=principal.workspace_id)

    return CurrentUserResponse(
        user=UserRead.model_validate(user),
        workspace=WorkspaceRead.model_validate(workspace),
        membership=MembershipRead.model_validate(membership),
        demo=demo,
    )


# Workspace routes
workspace_router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@workspace_router.get("/current", response_model=WorkspaceRead)
def get_current_workspace(
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceRead:
    """Get current workspace."""
    _, workspace_id, _ = current_user
    service = IdentityService(db)
    workspace = service.get_workspace_by_id(workspace_id)
    return WorkspaceRead.model_validate(workspace)


@workspace_router.get("/current/members", response_model=list[MembershipRead])
def get_workspace_members(
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_role([UserRole.ADMIN])),
    db: Session = Depends(get_db),
) -> list[MembershipRead]:
    """Get workspace members (admin only)."""
    _, workspace_id, _ = current_user
    service = IdentityService(db)
    members = service.get_members_for_workspace(workspace_id)
    return [MembershipRead.model_validate(m) for m in members]


@workspace_router.patch("/current/members/{user_id}", response_model=MembershipRead)
def update_workspace_member(
    user_id: UUID,
    membership_data: MembershipUpdate,
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_role([UserRole.ADMIN])),
    db: Session = Depends(get_db),
) -> MembershipRead:
    """Update a workspace member's role and/or status (admin only)."""
    _, workspace_id, _ = current_user
    service = IdentityService(db)
    try:
        membership = service.update_membership(user_id, workspace_id, membership_data)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return MembershipRead.model_validate(membership)


@workspace_router.post("/current/invitations", response_model=InvitationRead, status_code=status.HTTP_201_CREATED)
def create_invitation(
    invitation_data: InvitationCreate,
    current_user: tuple[UUID, UUID, UserRole] = Depends(require_role([UserRole.ADMIN])),
    db: Session = Depends(get_db),
) -> InvitationRead:
    """Create an invitation (admin only)."""
    user_id, workspace_id, _ = current_user
    service = IdentityService(db)
    try:
        invitation = service.create_invitation(workspace_id, invitation_data, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return InvitationRead.model_validate(invitation)


@auth_router.post("/accept-invitation", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def accept_invitation(
    invitation_data: AcceptInvitationRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Accept an invitation and create user account."""
    if settings.demo_public_mode:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Public invitations are disabled while public demo mode is enabled",
        )
    service = IdentityService(db)
    invitation = service.get_invitation_by_token(invitation_data.token)

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    if invitation.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation already accepted",
        )

    if datetime.now(UTC) > invitation.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation expired",
        )

    user = service.accept_invitation(invitation, invitation_data.password, invitation_data.full_name)

    # Create tokens for the new membership
    access_token, refresh_token = service.create_tokens(
        user, invitation.workspace_id, invitation.role
    )
    service.store_refresh_token(user.id, refresh_token)

    # Set cookies
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
    )


# Include both route groups under the API prefix. Auth endpoints retain their
# /auth prefix while workspace endpoints are available at /workspaces.
# Keep the original nested workspace URLs available while exposing the
# documented top-level /workspaces URLs.
auth_router.include_router(workspace_router)
router.include_router(auth_router)
router.include_router(workspace_router)
