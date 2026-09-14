from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.identity.models import Membership, MembershipStatus, UserRole
from app.modules.identity.router import get_current_user


class PermissionError(Exception):
    """Base exception for permission errors."""
    pass


class NotAuthenticatedError(PermissionError):
    """Raised when user is not authenticated."""
    pass


class NotAuthorizedError(PermissionError):
    """Raised when user lacks required permissions."""
    pass


class CrossWorkspaceError(PermissionError):
    """Raised when attempting to access resources from another workspace."""
    pass


def require_member(
    session: Session,
    user_id: UUID,
    workspace_id: UUID,
) -> Membership:
    """Get active membership or raise error."""
    membership = session.query(Membership).filter(
        Membership.user_id == user_id,
        Membership.workspace_id == workspace_id,
        Membership.status == MembershipStatus.ACTIVE,
    ).first()

    if not membership:
        raise NotAuthorizedError("No active membership in this workspace")

    return membership


def check_role(member: Membership, allowed_roles: list[UserRole]) -> None:
    """Check if member has one of the allowed roles."""
    if member.role not in allowed_roles:
        raise NotAuthorizedError(f"Required role: {', '.join(r.value for r in allowed_roles)}")


def can_view_run(member: Membership, run: Any) -> bool:
    """Check if member can view a prospection run."""
    if member.role == UserRole.ADMIN:
        return True
    # Setter and closer can view their own runs
    return bool(run.created_by_id == member.user_id)


def can_view_lead(member: Membership, lead: Any) -> bool:
    """Check if member can view a lead."""
    if member.role == UserRole.ADMIN:
        return True
    # Setter can view own or assigned leads
    if member.role == UserRole.SETTER:
        return bool(lead.setter_id == member.user_id)
    # Closer can view assigned leads
    if member.role == UserRole.CLOSER:
        return bool(lead.closer_id == member.user_id)
    return False


def can_manage_lead(member: Membership, lead: Any) -> bool:
    """Check if member can manage (edit/assign) a lead."""
    return member.role == UserRole.ADMIN


def can_assign_leads(member: Membership) -> bool:
    """Check if member can assign leads."""
    return member.role in (UserRole.ADMIN, UserRole.CLOSER)


def can_change_stage(member: Membership, lead: Any, new_stage: str) -> bool:
    """Check if member can change lead stage."""
    if member.role == UserRole.ADMIN:
        return True
    # Setter can change initial stages
    if member.role == UserRole.SETTER:
        # Cannot mark as won/lost
        return new_stage not in ("won", "lost", "not_interested")
    # Closer can change any stage
    return member.role == UserRole.CLOSER


def can_mark_won_lost(member: Membership) -> bool:
    """Check if member can mark lead as won/lost."""
    return member.role in (UserRole.ADMIN, UserRole.CLOSER)


def can_use_ai(member: Membership) -> bool:
    """Check if member can use AI features."""
    return member.role in (UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER)


def can_manage_rag(member: Membership) -> bool:
    """Check if member can manage RAG documents."""
    return member.role == UserRole.ADMIN


def can_manage_users(member: Membership) -> bool:
    """Check if member can manage users."""
    return member.role == UserRole.ADMIN


def can_view_audit(member: Membership) -> bool:
    """Check if member can view audit logs."""
    return member.role == UserRole.ADMIN


def get_current_membership(
    db: Session = Depends(get_db),
    current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user),
) -> Membership:
    """Get current user's membership in the workspace."""
    _, workspace_id, _ = current_user
    return require_member(db, current_user[0], workspace_id)


def require_role(member: Membership, allowed_roles: list[UserRole]) -> None:
    """Check if member has one of the allowed roles."""
    if member.role not in allowed_roles:
        raise NotAuthorizedError(f"Required role: {', '.join(r.value for r in allowed_roles)}")


def require_role_dependency(
    allowed_roles: list[UserRole | str],
) -> Callable[..., tuple[UUID, UUID, UserRole]]:
    """Dependency that checks if the current user has one of the allowed roles."""
    normalized_roles = {
        role if isinstance(role, UserRole) else UserRole(role) for role in allowed_roles
    }

    def role_checker(
        current_user: tuple[UUID, UUID, UserRole] = Depends(get_current_user),
    ) -> tuple[UUID, UUID, UserRole]:
        if current_user[2] not in normalized_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {', '.join(r.value for r in normalized_roles)}",
            )
        return current_user

    return role_checker
