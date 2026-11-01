from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import Base, LifeGraphChangeRequestRecord, UserRecord


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def add_user(database: Session, user_id: str, now: datetime) -> None:
    database.add(UserRecord(
        id=user_id,
        display_name="演示用户",
        phone_masked="138****0000",
        role="CUSTOMER",
        status="ACTIVE",
        risk_level="LOW",
        created_at=now,
        updated_at=now,
    ))
    database.flush()


def build_request(
    *, request_id: str, user_id: str, idempotency_key: str, now: datetime
) -> LifeGraphChangeRequestRecord:
    return LifeGraphChangeRequestRecord(
        request_id=request_id,
        owner_user_id=user_id,
        source_run_id=None,
        source="STRUCTURED_INPUT",
        status="PENDING",
        schema_version=1,
        idempotency_key=idempotency_key,
        snapshot_hash="a" * 64,
        summary="新增爱人生日和 1000 元礼物目标",
        change_set={
            "nodes": [{"client_ref": "spouse", "node_type": "PERSON"}],
            "edges": [{"source_ref": "self", "target_ref": "spouse"}],
            "events": [{"client_ref": "birthday", "event_type": "BIRTHDAY"}],
            "goals": [{"client_ref": "gift", "target_amount": "1000.00"}],
            "actions": [],
        },
        decision_reason=None,
        expires_at=now + timedelta(minutes=15),
        decided_at=None,
        created_at=now,
        updated_at=now,
    )


def test_change_request_stays_pending_with_a_tamper_evident_snapshot():
    engine = build_engine()
    now = datetime(2026, 10, 28, 14, 0, 0)

    with Session(engine) as database, database.begin():
        add_user(database, "user_001", now)
        database.add(build_request(
            request_id="change_001",
            user_id="user_001",
            idempotency_key="birthday-plan-001",
            now=now,
        ))

    with Session(engine) as database:
        request = database.get(LifeGraphChangeRequestRecord, "change_001")

    assert request is not None
    assert request.status == "PENDING"
    assert request.snapshot_hash == "a" * 64
    assert request.change_set["goals"][0]["target_amount"] == "1000.00"
    assert request.decided_at is None


def test_idempotency_key_is_unique_per_user_but_reusable_by_another_user():
    engine = build_engine()
    now = datetime(2026, 10, 28, 14, 0, 0)

    with Session(engine) as database, database.begin():
        add_user(database, "user_001", now)
        add_user(database, "user_002", now)
        database.add_all([
            build_request(
                request_id="change_user_1",
                user_id="user_001",
                idempotency_key="same-client-key",
                now=now,
            ),
            build_request(
                request_id="change_user_2",
                user_id="user_002",
                idempotency_key="same-client-key",
                now=now,
            ),
        ])

    with pytest.raises(IntegrityError):
        with Session(engine) as database, database.begin():
            database.add(build_request(
                request_id="change_duplicate",
                user_id="user_001",
                idempotency_key="same-client-key",
                now=now,
            ))


def test_change_request_rejects_unknown_status():
    engine = build_engine()
    now = datetime(2026, 10, 28, 14, 0, 0)

    with pytest.raises(IntegrityError):
        with Session(engine) as database, database.begin():
            add_user(database, "user_001", now)
            request = build_request(
                request_id="change_invalid",
                user_id="user_001",
                idempotency_key="invalid-status",
                now=now,
            )
            request.status = "EXECUTE_WITHOUT_CONFIRMATION"
            database.add(request)
