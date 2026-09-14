from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal, get_db
from app.main import app
from app.modules.identity.models import Membership, User, UserRole, Workspace
from app.modules.identity.router import get_current_user
from app.modules.seller_profile.models import SellerProfile


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


def make_member(db, role=UserRole.ADMIN):
    suffix = uuid4().hex
    user = User(email=f"seller-{suffix}@example.com", full_name="Seller", hashed_password="x")
    workspace = Workspace(name="Seller workspace", slug=f"seller-{suffix}")
    db.add_all([user, workspace])
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: (user.id, workspace.id, role)
    return user, workspace


def test_member_can_create_and_update_seller_profile(client, db):
    user, workspace = make_member(db)
    payload = {
        "business_name": "Tu Web MX",
        "description": "Agencia de sitios web",
        "offer_summary": "Sitios web para negocios locales",
        "target_customer_summary": "Negocios locales sin presencia propia",
        "location_or_market": "México",
        "price_context": "Desde $2,500 MXN",
        "primary_goal": "Conseguir conversaciones calificadas",
        "differentiators": "Implementación rápida",
        "sales_constraints": "No prometer resultados garantizados",
    }

    created = client.put("/api/v1/seller-profile", json=payload)
    assert created.status_code == 201
    assert created.json()["business_name"] == "Tu Web MX"

    updated = client.put(
        "/api/v1/seller-profile",
        json={"offer_summary": "Sitios web y automatización comercial"},
    )
    assert updated.status_code == 200
    assert updated.json()["offer_summary"] == "Sitios web y automatización comercial"
    assert db.execute(select(SellerProfile).where(SellerProfile.workspace_id == workspace.id)).scalar_one().updated_at


def test_seller_profile_isolated_between_workspaces(client, db):
    _, workspace_a = make_member(db)
    client.put("/api/v1/seller-profile", json={"business_name": "Workspace A"})
    _, workspace_b = make_member(db)
    assert client.get("/api/v1/seller-profile").status_code == 404
    client.put("/api/v1/seller-profile", json={"business_name": "Workspace B"})
    assert db.execute(select(SellerProfile).where(SellerProfile.workspace_id == workspace_a.id)).scalar_one().business_name == "Workspace A"
    assert db.execute(select(SellerProfile).where(SellerProfile.workspace_id == workspace_b.id)).scalar_one().business_name == "Workspace B"


def test_seller_profile_validates_business_name(client, db):
    make_member(db)
    response = client.put("/api/v1/seller-profile", json={"business_name": ""})
    assert response.status_code == 422
