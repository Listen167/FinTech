from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import (
    AccountRecord,
    Base,
    LifeGraphChangeRequestRecord,
    LifeGraphNodeRecord,
    UserRecord,
)
from backend.app.dependencies.auth import get_auth_service, get_current_auth
from backend.app.dependencies.life_graph import get_life_graph_service
from backend.app.dependencies.life_goal_conflicts import get_life_goal_conflict_service
from backend.app.main import app
from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.services.auth_service import AuthContext
from backend.app.services.life_graph_service import LifeGraphService
from backend.app.services.life_goal_conflict_service import LifeGoalConflictService


def build_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def auth_context(user_id: str) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        session_id=f"session_{user_id}",
        token_id=f"token_{user_id}",
        credential_version=1,
        display_name="演示用户",
        phone_masked="138****0000",
        role="CUSTOMER",
        risk_level="LOW",
    )


def seed_users(engine, now: datetime) -> None:
    with Session(engine) as database, database.begin():
        database.add_all([
            UserRecord(
                id="user_001",
                display_name="林安",
                phone_masked="138****1024",
                role="CUSTOMER",
                status="ACTIVE",
                risk_level="LOW",
                created_at=now,
                updated_at=now,
            ),
            UserRecord(
                id="user_002",
                display_name="周宁",
                phone_masked="139****2048",
                role="CUSTOMER",
                status="ACTIVE",
                risk_level="LOW",
                created_at=now,
                updated_at=now,
            ),
        ])
        database.flush()
        database.add(LifeGraphNodeRecord(
            node_id="person_self_001",
            owner_user_id="user_001",
            node_type="PERSON",
            display_label="本人",
            sensitivity_level="PERSONAL",
            status="ACTIVE",
            attributes={"is_account_owner": True},
            created_at=now,
            updated_at=now,
        ))
        database.add(AccountRecord(
            id="acct_001",
            user_id="user_001",
            payload={
                "id": "acct_001",
                "user_id": "user_001",
                "currency": "CNY",
                "available_balance": 10000,
            },
        ))


def change_payload(amount: str = "1000.00") -> dict:
    return {
        "summary": f"建立 {amount} 元生日礼物目标",
        "change_set": {
            "nodes": [
                {
                    "node_id": "goal_gift_api_001",
                    "node_type": "GOAL",
                    "display_label": "生日礼物心愿金",
                    "sensitivity_level": "PERSONAL",
                    "attributes": {},
                }
            ],
            "edges": [],
            "events": [],
            "goals": [
                {
                    "goal_node_id": "goal_gift_api_001",
                    "target_amount": amount,
                    "reserved_amount": "0.00",
                    "currency": "CNY",
                    "due_at": "2026-11-18T00:00:00",
                    "funding_policy": {"mode": "RESERVE_AFTER_CONFIRMATION"},
                }
            ],
            "actions": [],
        },
    }


@pytest.fixture
def api_setup():
    engine = build_engine()
    now = datetime(2026, 10, 28, 17, 0, 0)
    seed_users(engine, now)
    service = LifeGraphService(engine, LifeGraphRepository(engine))
    conflict_service = LifeGoalConflictService(engine, service.repository)
    app.dependency_overrides[get_life_graph_service] = lambda: service
    app.dependency_overrides[get_life_goal_conflict_service] = lambda: conflict_service
    app.dependency_overrides[get_current_auth] = lambda: auth_context("user_001")
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        app.dependency_overrides.clear()


def test_create_replay_approve_and_query_graph(api_setup):
    client, _ = api_setup
    headers = {"Idempotency-Key": "api-birthday-001"}

    created = client.post("/api/life-graph/change-requests", json=change_payload(), headers=headers)
    replay = client.post("/api/life-graph/change-requests", json=change_payload(), headers=headers)
    request_id = created.json()["request"]["request_id"]
    approved = client.post(
        f"/api/life-graph/change-requests/{request_id}/decide",
        json={"approved": True},
    )
    graph = client.get("/api/life-graph")

    assert created.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["request"]["request_id"] == request_id
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert len(graph.json()["goals"]) == 1


def test_idempotency_conflict_and_replay_return_409(api_setup):
    client, _ = api_setup
    headers = {"Idempotency-Key": "api-conflict-001"}
    created = client.post("/api/life-graph/change-requests", json=change_payload(), headers=headers)
    request_id = created.json()["request"]["request_id"]

    conflict = client.post(
        "/api/life-graph/change-requests",
        json=change_payload("2000.00"),
        headers=headers,
    )
    rejected = client.post(
        f"/api/life-graph/change-requests/{request_id}/decide",
        json={"approved": False, "reason": "用户取消"},
    )
    replay = client.post(
        f"/api/life-graph/change-requests/{request_id}/decide",
        json={"approved": True},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_CONTENT"
    assert rejected.status_code == 200
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "CHANGE_REQUEST_REPLAYED"


def test_cross_user_decision_returns_403(api_setup):
    client, _ = api_setup
    created = client.post(
        "/api/life-graph/change-requests",
        json=change_payload(),
        headers={"Idempotency-Key": "api-private-001"},
    )
    request_id = created.json()["request"]["request_id"]
    app.dependency_overrides[get_current_auth] = lambda: auth_context("user_002")

    response = client.post(
        f"/api/life-graph/change-requests/{request_id}/decide",
        json={"approved": True},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "RESOURCE_FORBIDDEN"


def test_tampered_request_returns_409_and_writes_nothing(api_setup):
    client, engine = api_setup
    created = client.post(
        "/api/life-graph/change-requests",
        json=change_payload(),
        headers={"Idempotency-Key": "api-tampered-001"},
    )
    request_id = created.json()["request"]["request_id"]
    with Session(engine) as database, database.begin():
        request = database.get(LifeGraphChangeRequestRecord, request_id)
        changed = dict(request.change_set)
        changed["goals"] = [dict(changed["goals"][0], target_amount="9999.00")]
        request.change_set = changed

    response = client.post(
        f"/api/life-graph/change-requests/{request_id}/decide",
        json={"approved": True},
    )
    graph = client.get("/api/life-graph").json()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CHANGE_REQUEST_TAMPERED"
    assert graph["goals"] == []


def test_anonymous_request_returns_401(api_setup):
    client, _ = api_setup
    app.dependency_overrides.pop(get_current_auth)
    app.dependency_overrides[get_auth_service] = lambda: object()

    response = client.get("/api/life-graph")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "NOT_AUTHENTICATED"


def test_candidate_goal_conflict_endpoint_is_user_scoped(api_setup):
    client, _ = api_setup
    created = client.post(
        "/api/life-graph/change-requests",
        json=change_payload(),
        headers={"Idempotency-Key": "api-conflict-analysis-001"},
    )
    request_id = created.json()["request"]["request_id"]

    analysis = client.get(
        f"/api/life-graph/change-requests/{request_id}/goal-conflicts"
    )
    app.dependency_overrides[get_current_auth] = lambda: auth_context("user_002")
    cross_user = client.get(
        f"/api/life-graph/change-requests/{request_id}/goal-conflicts"
    )

    assert analysis.status_code == 200
    assert analysis.json()["status"] == "CLEAR"
    assert analysis.json()["priority_decision"] == "USER_REQUIRED"
    assert cross_user.status_code == 404
    assert cross_user.json()["detail"]["code"] == "CHANGE_REQUEST_NOT_FOUND"


def test_node_correction_has_stable_validation_and_idempotency_errors(api_setup):
    client, _ = api_setup
    headers = {"Idempotency-Key": "api-correction-001"}

    corrected = client.patch(
        "/api/life-graph/nodes/person_self_001",
        json={"display_label": "林安本人", "reason": "补充准确显示名称"},
        headers=headers,
    )
    conflicting_replay = client.patch(
        "/api/life-graph/nodes/person_self_001",
        json={"display_label": "另一个名字", "reason": "重复使用幂等键"},
        headers=headers,
    )
    invalid = client.patch(
        "/api/life-graph/nodes/person_self_001",
        json={"display_label": "缺少原因"},
        headers={"Idempotency-Key": "api-correction-invalid"},
    )

    assert corrected.status_code == 200
    assert corrected.json()["graph"]["nodes"][0]["display_label"] == "林安本人"
    assert conflicting_replay.status_code == 409
    assert conflicting_replay.json()["detail"]["code"] == "CORRECTION_IDEMPOTENCY_CONFLICT"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "INVALID_REQUEST"
    assert invalid.json()["detail"]["details"]["fields"]
