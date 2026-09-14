from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.session import SessionLocal, get_db
from app.main import app
from app.modules.activities.models import Activity, ActivityType, FollowUpTask
from app.modules.identity.models import Membership, User, UserRole, Workspace
from app.modules.identity.router import get_current_user
from app.modules.leads.models import Business, Lead
from app.modules.pipeline.models import PipelineStage


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


def make_workspace(db):
    suffix = uuid4().hex
    admin = User(email=f"admin-{suffix}@example.com", full_name="Admin", hashed_password="x")
    workspace = Workspace(name="Workspace", slug=f"workspace-{suffix}")
    db.add_all([admin, workspace])
    db.flush()
    db.add(Membership(user_id=admin.id, workspace_id=workspace.id, role=UserRole.ADMIN))
    db.commit()
    return admin, workspace


def add_member(db, workspace, role=UserRole.CLOSER):
    user = User(email=f"member-{uuid4().hex}@example.com", full_name="Member", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    db.commit()
    return user


def add_lead(db, workspace, *, setter_id=None, closer_id=None, stage=None):
    business = Business(
        workspace_id=workspace.id,
        place_id=uuid4().hex,
        name=f"Business {uuid4().hex}",
    )
    db.add(business)
    db.flush()
    lead = Lead(
        workspace_id=workspace.id,
        business_id=business.id,
        setter_id=setter_id,
        closer_id=closer_id,
        pipeline_stage_id=stage.id if stage else None,
    )
    db.add(lead)
    db.commit()
    return lead


def override_user(user, workspace):
    app.dependency_overrides[get_current_user] = lambda: (user.id, workspace.id, UserRole.ADMIN)


def test_admin_summary_is_workspace_scoped_and_includes_zero_workload_members(client, db):
    admin, workspace = make_workspace(db)
    zero_member = add_member(db, workspace)
    other_admin, other_workspace = make_workspace(db)
    stage = PipelineStage(workspace_id=workspace.id, key="new", label="Nuevo", order=0)
    db.add(stage)
    db.commit()
    add_lead(db, workspace, stage=stage)
    add_lead(db, other_workspace)
    overdue = FollowUpTask(
        workspace_id=workspace.id,
        assignee_id=zero_member.id,
        created_by_id=admin.id,
        title="Overdue",
        due_at=datetime.now(UTC) - timedelta(days=1),
    )
    today = FollowUpTask(
        workspace_id=workspace.id,
        assignee_id=zero_member.id,
        created_by_id=admin.id,
        title="Today",
        due_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add_all([overdue, today])
    db.commit()
    override_user(admin, workspace)

    response = client.get("/api/v1/admin/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["unassigned_leads"] == 1
    assert body["overdue_tasks"] == 1
    assert body["due_today"] == 1
    assert {item["id"] for item in body["member_workload"]} >= {str(admin.id), str(zero_member.id)}
    assert next(item for item in body["member_workload"] if item["id"] == str(zero_member.id))["lead_count"] == 0
    assert all(item["id"] != str(other_admin.id) for item in body["member_workload"])


def test_summary_rejects_setter(client, db):
    admin, workspace = make_workspace(db)
    setter = add_member(db, workspace, UserRole.SETTER)
    app.dependency_overrides[get_current_user] = lambda: (setter.id, workspace.id, UserRole.SETTER)

    assert client.get("/api/v1/admin/summary").status_code == 403


def test_bulk_assignment_creates_one_audit_activity_per_changed_lead(client, db):
    admin, workspace = make_workspace(db)
    closer = add_member(db, workspace)
    lead_a = add_lead(db, workspace)
    lead_b = add_lead(db, workspace, closer_id=closer.id)
    override_user(admin, workspace)

    response = client.post(
        "/api/v1/leads/bulk-assign",
        json={"lead_ids": [str(lead_a.id), str(lead_b.id)], "closer_id": str(closer.id)},
    )

    assert response.status_code == 201
    activities = db.execute(
        select(Activity).where(
            Activity.workspace_id == workspace.id,
            Activity.type == ActivityType.ASSIGNMENT,
        )
    ).scalars().all()
    assert len(activities) == 1
    assert activities[0].lead_id == lead_a.id
    assert activities[0].author_id == admin.id
    assert str(closer.id) in activities[0].content
    assert activities[0].created_at.tzinfo is not None


def test_bulk_assignment_with_no_changes_creates_no_activity(client, db):
    admin, workspace = make_workspace(db)
    closer = add_member(db, workspace)
    lead = add_lead(db, workspace, closer_id=closer.id)
    override_user(admin, workspace)

    response = client.post(
        "/api/v1/leads/bulk-assign",
        json={"lead_ids": [str(lead.id)], "closer_id": str(closer.id)},
    )

    assert response.status_code == 201
    assert db.execute(select(func.count(Activity.id)).where(Activity.type == ActivityType.ASSIGNMENT)).scalar_one() == 0
