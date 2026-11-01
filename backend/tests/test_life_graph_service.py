from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.db.models import Base, LifeGraphChangeRequestRecord, LifeGraphNodeRecord, UserRecord
from backend.app.repositories.life_graph import LifeGraphRepository, NewLifeGraphChangeRequest
from backend.app.services.life_graph_service import LifeGraphService, LifeGraphServiceError


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


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


def birthday_change_set(*, duplicate_node: bool = False) -> dict:
    spouse_node_id = "person_self_001" if duplicate_node else "person_spouse_001"
    return {
        "nodes": [
            {
                "node_id": spouse_node_id,
                "node_type": "PERSON",
                "display_label": "爱人",
                "sensitivity_level": "SENSITIVE",
                "attributes": {},
            },
            {
                "node_id": "event_birthday_001",
                "node_type": "EVENT",
                "display_label": "爱人生日",
                "sensitivity_level": "SENSITIVE",
                "attributes": {},
            },
            {
                "node_id": "goal_gift_001",
                "node_type": "GOAL",
                "display_label": "生日礼物心愿金",
                "sensitivity_level": "PERSONAL",
                "attributes": {},
            },
        ],
        "edges": [
            {
                "edge_id": "edge_spouse_001",
                "source_node_id": "person_self_001",
                "target_node_id": spouse_node_id,
                "relation_type": "SPOUSE_OF",
                "consent_scope": {"allowed_uses": ["GOAL_PLANNING"]},
                "provenance": "USER_CONFIRMED",
                "confidence_bp": 10000,
                "attributes": {},
            },
            {
                "edge_id": "edge_spouse_birthday_001",
                "source_node_id": spouse_node_id,
                "target_node_id": "event_birthday_001",
                "relation_type": "HAS_EVENT",
                "consent_scope": {"allowed_uses": ["REMINDER", "GOAL_PLANNING"]},
                "provenance": "USER_CONFIRMED",
                "confidence_bp": 10000,
                "attributes": {},
            },
            {
                "edge_id": "edge_goal_event_001",
                "source_node_id": "goal_gift_001",
                "target_node_id": "event_birthday_001",
                "relation_type": "TRIGGERED_BY",
                "consent_scope": {"allowed_uses": ["GOAL_PLANNING"]},
                "provenance": "USER_CONFIRMED",
                "confidence_bp": 10000,
                "attributes": {},
            },
        ],
        "events": [
            {
                "event_node_id": "event_birthday_001",
                "event_type": "BIRTHDAY",
                "occurs_at": "2026-11-18T00:00:00",
                "timezone": "Asia/Shanghai",
                "recurrence_rule": "FREQ=YEARLY",
                "details": {"remind_days_before": 2},
            }
        ],
        "goals": [
            {
                "goal_node_id": "goal_gift_001",
                "target_amount": "1000.00",
                "reserved_amount": "0.00",
                "currency": "CNY",
                "due_at": "2026-11-18T00:00:00",
                "funding_policy": {"mode": "RESERVE_AFTER_CONFIRMATION"},
            }
        ],
        "actions": [
            {
                "action_id": "action_remind_001",
                "event_node_id": "event_birthday_001",
                "goal_node_id": "goal_gift_001",
                "action_type": "REMIND_PURCHASE_PLAN",
                "scheduled_at": "2026-11-16T09:00:00",
                "idempotency_key": "birthday-2026-reminder",
                "payload": {"suggestions": ["鲜花", "蛋糕"]},
            }
        ],
    }


def create_request(
    repository: LifeGraphRepository,
    *,
    request_id: str,
    now: datetime,
    change_set: dict | None = None,
    expires_at: datetime | None = None,
) -> None:
    repository.create_change_request(NewLifeGraphChangeRequest(
        request_id=request_id,
        owner_user_id="user_001",
        idempotency_key=request_id,
        summary="新增爱人生日、礼物目标和提醒",
        change_set=change_set or birthday_change_set(),
        expires_at=expires_at or now + timedelta(minutes=15),
        created_at=now,
    ))


def test_approval_atomically_materializes_the_complete_graph():
    engine = build_engine()
    now = datetime(2026, 10, 28, 16, 0, 0)
    seed_users(engine, now)
    repository = LifeGraphRepository(engine)
    create_request(repository, request_id="change_approve", now=now)
    service = LifeGraphService(engine, repository)

    result = service.decide_change_request(
        owner_user_id="user_001",
        request_id="change_approve",
        approved=True,
        now=now + timedelta(minutes=1),
    )
    graph = repository.get_graph("user_001")

    assert result["status"] == "APPROVED"
    assert len(graph["nodes"]) == 4
    assert len(graph["edges"]) == 3
    assert graph["goals"][0]["target_amount"] == "1000.00"
    assert graph["actions"][0]["status"] == "PENDING"


def test_rejection_does_not_modify_the_formal_graph():
    engine = build_engine()
    now = datetime(2026, 10, 28, 16, 0, 0)
    seed_users(engine, now)
    repository = LifeGraphRepository(engine)
    create_request(repository, request_id="change_reject", now=now)
    service = LifeGraphService(engine, repository)

    result = service.decide_change_request(
        owner_user_id="user_001",
        request_id="change_reject",
        approved=False,
        reason="暂时不需要",
        now=now + timedelta(minutes=1),
    )

    assert result["status"] == "REJECTED"
    assert result["decision_reason"] == "暂时不需要"
    assert len(repository.get_graph("user_001")["nodes"]) == 1


def test_cross_user_decision_and_replay_are_rejected():
    engine = build_engine()
    now = datetime(2026, 10, 28, 16, 0, 0)
    seed_users(engine, now)
    repository = LifeGraphRepository(engine)
    create_request(repository, request_id="change_private", now=now)
    service = LifeGraphService(engine, repository)

    with pytest.raises(LifeGraphServiceError, match="RESOURCE_FORBIDDEN"):
        service.decide_change_request(
            owner_user_id="user_002",
            request_id="change_private",
            approved=True,
            now=now + timedelta(minutes=1),
        )

    service.decide_change_request(
        owner_user_id="user_001",
        request_id="change_private",
        approved=False,
        now=now + timedelta(minutes=1),
    )
    with pytest.raises(LifeGraphServiceError, match="CHANGE_REQUEST_REPLAYED"):
        service.decide_change_request(
            owner_user_id="user_001",
            request_id="change_private",
            approved=True,
            now=now + timedelta(minutes=2),
        )


def test_tampered_snapshot_is_canceled_without_graph_changes():
    engine = build_engine()
    now = datetime(2026, 10, 28, 16, 0, 0)
    seed_users(engine, now)
    repository = LifeGraphRepository(engine)
    create_request(repository, request_id="change_tampered", now=now)
    with Session(engine) as database, database.begin():
        request = database.get(LifeGraphChangeRequestRecord, "change_tampered")
        changed = dict(request.change_set)
        changed["goals"] = [dict(changed["goals"][0], target_amount="9999.00")]
        request.change_set = changed
    service = LifeGraphService(engine, repository)

    with pytest.raises(LifeGraphServiceError, match="CHANGE_REQUEST_TAMPERED"):
        service.decide_change_request(
            owner_user_id="user_001",
            request_id="change_tampered",
            approved=True,
            now=now + timedelta(minutes=1),
        )

    assert repository.get_change_request("user_001", "change_tampered")["status"] == "CANCELED"
    assert len(repository.get_graph("user_001")["nodes"]) == 1


def test_database_conflict_rolls_back_every_graph_write_and_keeps_request_pending():
    engine = build_engine()
    now = datetime(2026, 10, 28, 16, 0, 0)
    seed_users(engine, now)
    repository = LifeGraphRepository(engine)
    create_request(
        repository,
        request_id="change_conflict",
        now=now,
        change_set=birthday_change_set(duplicate_node=True),
    )
    service = LifeGraphService(engine, repository)

    with pytest.raises(LifeGraphServiceError, match="NODE_ALREADY_EXISTS"):
        service.decide_change_request(
            owner_user_id="user_001",
            request_id="change_conflict",
            approved=True,
            now=now + timedelta(minutes=1),
        )

    assert repository.get_change_request("user_001", "change_conflict")["status"] == "PENDING"
    assert len(repository.get_graph("user_001")["nodes"]) == 1


def test_expired_request_is_persisted_as_expired():
    engine = build_engine()
    now = datetime(2026, 10, 28, 16, 0, 0)
    seed_users(engine, now)
    repository = LifeGraphRepository(engine)
    create_request(
        repository,
        request_id="change_expired",
        now=now,
        expires_at=now + timedelta(minutes=1),
    )
    service = LifeGraphService(engine, repository)

    with pytest.raises(LifeGraphServiceError, match="CHANGE_REQUEST_EXPIRED"):
        service.decide_change_request(
            owner_user_id="user_001",
            request_id="change_expired",
            approved=True,
            now=now + timedelta(minutes=2),
        )

    assert repository.get_change_request("user_001", "change_expired")["status"] == "EXPIRED"
    assert len(repository.get_graph("user_001")["nodes"]) == 1


def test_reset_owner_clears_only_the_selected_users_life_graph():
    engine = build_engine()
    now = datetime(2026, 10, 28, 16, 0, 0)
    seed_users(engine, now)
    repository = LifeGraphRepository(engine)
    create_request(repository, request_id="change_reset", now=now)
    with Session(engine) as database, database.begin():
        database.add(LifeGraphNodeRecord(
            node_id="person_self_002",
            owner_user_id="user_002",
            node_type="PERSON",
            display_label="本人",
            sensitivity_level="PERSONAL",
            status="ACTIVE",
            attributes={"is_account_owner": True},
            created_at=now,
            updated_at=now,
        ))

    deleted = LifeGraphService(engine, repository).reset_owner("user_001")

    assert deleted["life_graph_nodes"] == 1
    assert deleted["life_graph_change_requests"] == 1
    assert repository.get_graph("user_001")["nodes"] == []
    assert repository.list_change_requests("user_001") == []
    assert [
        item["node_id"] for item in repository.get_graph("user_002")["nodes"]
    ] == ["person_self_002"]
