from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.security import create_access_token
from app.db.session import SessionLocal, get_db
from app.main import app
from app.modules.identity.models import (
    Invitation,
    Membership,
    MembershipStatus,
    User,
    UserRole,
    Workspace,
)
from app.modules.identity.router import get_current_user
from app.modules.identity.schemas import InvitationCreate, MembershipUpdate
from app.modules.identity.service import IdentityService


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def make_workspace(db, *, role=UserRole.ADMIN):
    suffix = uuid4().hex
    owner = User(
        email=f"owner-{suffix}@example.com",
        full_name="Workspace Owner",
        hashed_password="not-used",
    )
    workspace = Workspace(name=f"Workspace {suffix}", slug=f"workspace-{suffix}")
    db.add_all([owner, workspace])
    db.flush()
    membership = Membership(
        user_id=owner.id,
        workspace_id=workspace.id,
        role=role,
        status=MembershipStatus.ACTIVE,
    )
    db.add(membership)
    db.commit()
    return owner, workspace, membership


def add_member(db, workspace, *, role=UserRole.SETTER, status=MembershipStatus.ACTIVE):
    suffix = uuid4().hex
    user = User(
        email=f"member-{suffix}@example.com",
        full_name="Workspace Member",
        hashed_password="not-used",
    )
    db.add(user)
    db.flush()
    membership = Membership(
        user_id=user.id,
        workspace_id=workspace.id,
        role=role,
        status=status,
    )
    db.add(membership)
    db.commit()
    return user, membership


def current_user_override(user_id, workspace_id, role):
    return lambda: (user_id, workspace_id, role)


def test_membership_update_accepts_partial_role_and_status():
    assert MembershipUpdate(role=UserRole.CLOSER).role is UserRole.CLOSER
    assert MembershipUpdate(status=MembershipStatus.INACTIVE).status is MembershipStatus.INACTIVE


def test_admin_can_deactivate_member_but_not_last_admin(client, db):
    admin, workspace, _ = make_workspace(db)
    member, _ = add_member(db, workspace)
    app.dependency_overrides[get_current_user] = current_user_override(
        admin.id, workspace.id, UserRole.ADMIN
    )

    response = client.patch(
        f"/api/v1/workspaces/current/members/{member.id}",
        json={"status": "inactive"},
    )
    assert response.status_code == 200
    assert db.execute(
        select(Membership.status).where(Membership.user_id == member.id)
    ).scalar_one() is MembershipStatus.INACTIVE

    response = client.patch(
        f"/api/v1/workspaces/current/members/{admin.id}",
        json={"status": "inactive"},
    )
    assert response.status_code == 422


def test_setter_cannot_change_workspace_member(client, db):
    setter, workspace, _ = make_workspace(db, role=UserRole.SETTER)
    member, _ = add_member(db, workspace)
    app.dependency_overrides[get_current_user] = current_user_override(
        setter.id, workspace.id, UserRole.SETTER
    )

    response = client.patch(
        f"/api/v1/workspaces/current/members/{member.id}",
        json={"role": "closer"},
    )
    assert response.status_code == 403


def test_current_user_uses_membership_role_after_jwt_role_changes(db):
    user, workspace, membership = make_workspace(db)
    membership.role = UserRole.SETTER
    db.commit()
    token = create_access_token(
        {"sub": str(user.id), "workspace_id": str(workspace.id), "role": UserRole.ADMIN.value}
    )
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/workspaces/current/members",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_workspace_membership_update_is_scoped_to_token_workspace(client, db):
    admin_a, workspace_a, _ = make_workspace(db)
    _, workspace_b, _ = make_workspace(db)
    member_b, _ = add_member(db, workspace_b)
    app.dependency_overrides[get_current_user] = current_user_override(
        admin_a.id, workspace_a.id, UserRole.ADMIN
    )

    response = client.patch(
        f"/api/v1/workspaces/current/members/{member_b.id}",
        json={"status": "inactive"},
    )
    assert response.status_code == 404


def test_invitation_has_seven_day_ttl_and_live_duplicate_is_conflict(client, db):
    admin, workspace, _ = make_workspace(db)
    app.dependency_overrides[get_current_user] = current_user_override(
        admin.id, workspace.id, UserRole.ADMIN
    )
    email = f"invite-{uuid4().hex}@example.com"

    first = client.post(
        "/api/v1/workspaces/current/invitations",
        json={"email": email, "role": "setter"},
    )
    assert first.status_code == 201
    invitation = db.get(Invitation, first.json()["id"])
    assert invitation is not None
    assert timedelta(days=6, hours=23) < invitation.expires_at - invitation.created_at <= timedelta(days=7, seconds=1)

    duplicate = client.post(
        "/api/v1/workspaces/current/invitations",
        json={"email": email, "role": "closer"},
    )
    assert duplicate.status_code == 409


def test_expired_invitation_is_replaced_by_new_record(db):
    admin, workspace, _ = make_workspace(db)
    service = IdentityService(db)
    data = InvitationCreate(email=f"expired-{uuid4().hex}@example.com", role=UserRole.SETTER)
    original = service.create_invitation(workspace.id, data, admin.id)
    original.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    replacement = service.create_invitation(workspace.id, data, admin.id)
    db.commit()
    assert replacement.id != original.id
    assert original.accepted_at is None
    assert original.superseded_at is not None
    assert replacement.expires_at > datetime.now(UTC)


def test_accepted_invitation_is_not_reactivated(db):
    admin, workspace, _ = make_workspace(db)
    service = IdentityService(db)
    data = InvitationCreate(email=f"used-{uuid4().hex}@example.com", role=UserRole.SETTER)
    invitation = service.create_invitation(workspace.id, data, admin.id)
    service.accept_invitation(invitation, "password-123", "Accepted User")
    db.commit()
    accepted_at = invitation.accepted_at

    replacement = service.create_invitation(workspace.id, data, admin.id)
    db.commit()
    assert replacement.id != invitation.id
    assert invitation.accepted_at == accepted_at


def test_accepted_invitation_token_cannot_be_reused(client, db):
    admin, workspace, _ = make_workspace(db)
    service = IdentityService(db)
    invitation = service.create_invitation(
        workspace.id,
        InvitationCreate(email=f"reused-{uuid4().hex}@example.com", role=UserRole.SETTER),
        admin.id,
    )
    service.accept_invitation(invitation, "password-123", "Accepted User")
    db.commit()

    response = client.post(
        "/api/v1/auth/accept-invitation",
        json={
            "token": invitation.token,
            "password": "password-123",
            "full_name": "Accepted User Again",
        },
    )
    assert response.status_code == 400


def test_accept_invitation_for_existing_member_does_not_create_duplicate(client, db):
    admin, workspace, _ = make_workspace(db)
    existing, _ = add_member(db, workspace, role=UserRole.SETTER)
    service = IdentityService(db)
    invitation = service.create_invitation(
        workspace.id,
        InvitationCreate(email=existing.email, role=UserRole.CLOSER),
        admin.id,
    )
    db.commit()

    response = client.post(
        "/api/v1/auth/accept-invitation",
        json={
            "token": invitation.token,
            "password": "password-123",
            "full_name": "Existing User",
        },
    )
    assert response.status_code == 201
    assert db.execute(
        select(func.count(Membership.id)).where(
            Membership.user_id == existing.id,
            Membership.workspace_id == workspace.id,
        )
    ).scalar_one() == 1


def test_invitation_insert_integrity_error_is_contract_conflict(db, monkeypatch):
    admin, workspace, _ = make_workspace(db)
    service = IdentityService(db)
    original_flush = db.flush
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IntegrityError("insert", {}, Exception("duplicate"))
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", fail_once)
    with pytest.raises(ValueError, match="pending invitation"):
        service.create_invitation(
            workspace.id,
            InvitationCreate(email=f"race-{uuid4().hex}@example.com", role=UserRole.SETTER),
            admin.id,
        )
