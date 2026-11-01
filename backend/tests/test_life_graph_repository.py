from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.db.models import Base, LifeGraphNodeRecord, UserRecord
from backend.app.repositories.life_graph import (
    IdempotencyConflictError,
    LifeGraphRepository,
    NewLifeGraphChangeRequest,
)


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def seed_users_and_nodes(engine, now: datetime) -> None:
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
        database.add_all([
            LifeGraphNodeRecord(
                node_id="person_001",
                owner_user_id="user_001",
                node_type="PERSON",
                display_label="林安本人",
                sensitivity_level="PERSONAL",
                status="ACTIVE",
                attributes={"is_account_owner": True},
                created_at=now,
                updated_at=now,
            ),
            LifeGraphNodeRecord(
                node_id="person_002",
                owner_user_id="user_002",
                node_type="PERSON",
                display_label="周宁本人",
                sensitivity_level="PERSONAL",
                status="ACTIVE",
                attributes={"is_account_owner": True},
                created_at=now,
                updated_at=now,
            ),
        ])


def build_draft(
    *,
    request_id: str,
    user_id: str,
    idempotency_key: str,
    now: datetime,
    amount: str = "1000.00",
) -> NewLifeGraphChangeRequest:
    return NewLifeGraphChangeRequest(
        request_id=request_id,
        owner_user_id=user_id,
        idempotency_key=idempotency_key,
        summary=f"建立 {amount} 元生日目标",
        change_set={
            "nodes": [{"client_ref": "spouse", "node_type": "PERSON"}],
            "edges": [],
            "events": [],
            "goals": [{"client_ref": "gift", "target_amount": amount}],
            "actions": [],
        },
        expires_at=now + timedelta(minutes=15),
        created_at=now,
    )


def test_graph_query_is_always_scoped_to_owner():
    engine = build_engine()
    now = datetime(2026, 10, 28, 15, 0, 0)
    seed_users_and_nodes(engine, now)
    repository = LifeGraphRepository(engine)

    user_1_graph = repository.get_graph("user_001")
    user_2_graph = repository.get_graph("user_002")

    assert [node["node_id"] for node in user_1_graph["nodes"]] == ["person_001"]
    assert [node["node_id"] for node in user_2_graph["nodes"]] == ["person_002"]
    assert "owner_user_id" not in user_1_graph["nodes"][0]


def test_same_idempotent_request_returns_original_record():
    engine = build_engine()
    now = datetime(2026, 10, 28, 15, 0, 0)
    seed_users_and_nodes(engine, now)
    repository = LifeGraphRepository(engine)

    first = repository.create_change_request(build_draft(
        request_id="change_first",
        user_id="user_001",
        idempotency_key="birthday-001",
        now=now,
    ))
    replay = repository.create_change_request(build_draft(
        request_id="change_replay",
        user_id="user_001",
        idempotency_key="birthday-001",
        now=now,
    ))

    assert first["created"] is True
    assert replay["created"] is False
    assert replay["request"]["request_id"] == "change_first"
    assert len(repository.list_change_requests("user_001")) == 1


def test_idempotency_key_reuse_with_different_content_is_rejected():
    engine = build_engine()
    now = datetime(2026, 10, 28, 15, 0, 0)
    seed_users_and_nodes(engine, now)
    repository = LifeGraphRepository(engine)
    repository.create_change_request(build_draft(
        request_id="change_first",
        user_id="user_001",
        idempotency_key="birthday-001",
        now=now,
    ))

    with pytest.raises(
        IdempotencyConflictError,
        match="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_CONTENT",
    ):
        repository.create_change_request(build_draft(
            request_id="change_changed",
            user_id="user_001",
            idempotency_key="birthday-001",
            now=now,
            amount="2000.00",
        ))


def test_change_request_cannot_be_read_by_another_owner():
    engine = build_engine()
    now = datetime(2026, 10, 28, 15, 0, 0)
    seed_users_and_nodes(engine, now)
    repository = LifeGraphRepository(engine)
    repository.create_change_request(build_draft(
        request_id="change_private",
        user_id="user_001",
        idempotency_key="private-001",
        now=now,
    ))

    assert repository.get_change_request("user_001", "change_private") is not None
    assert repository.get_change_request("user_002", "change_private") is None
    assert repository.list_change_requests("user_002") == []
