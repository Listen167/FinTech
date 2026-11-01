"""主动生活事件雷达的租约、投递、安全和 API 测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import (
    AccountRecord,
    Base,
    ChannelIdentityRecord,
    EventActionRecord,
    FinancialGoalRecord,
    LifeEventRecord,
    LifeGraphNodeRecord,
    UserRecord,
)
from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.life_event_radar import get_life_event_radar_service
from backend.app.errors import ApplicationError
from backend.app.main import app
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.repositories.life_event_radar import LifeEventRadarRepository
from backend.app.security.channel_identity_cipher import ChannelIdentityCipher
from backend.app.services.auth_service import AuthContext
from backend.app.services.life_event_radar import LifeEventRadarService


PEPPER = "radar-test-pepper-at-least-32-characters"


class FakeSender:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[dict] = []

    def send_text(self, *, receive_id: str, text: str, idempotency_key: str) -> dict:
        self.calls.append({
            "receive_id": receive_id,
            "text": text,
            "idempotency_key": idempotency_key,
        })
        if len(self.calls) <= self.failures:
            raise RuntimeError("provider timeout")
        return {"message_id": f"om_{idempotency_key}"}


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


def auth_context(user_id: str = "user_001") -> AuthContext:
    return AuthContext(
        user_id=user_id,
        session_id=f"auth_{user_id}",
        token_id=f"token_{user_id}",
        credential_version=1,
        display_name="演示用户",
        phone_masked="138****0000",
        role="CUSTOMER",
        risk_level="LOW",
    )


def seed_radar(
    engine,
    *,
    now: datetime,
    action_status: str = "PENDING",
    max_attempts: int = 3,
    event_status: str = "ACTIVE",
    balance: str = "500.00",
    with_delivery_identity: bool = True,
) -> ChannelIdentityCipher:
    current = now.astimezone(timezone.utc).replace(tzinfo=None)
    cipher = ChannelIdentityCipher(PEPPER)
    with Session(engine) as database, database.begin():
        database.add_all([
            UserRecord(
                id="user_001", display_name="林安", phone_masked="138****1024",
                role="CUSTOMER", status="ACTIVE", risk_level="LOW",
                created_at=current, updated_at=current,
            ),
            UserRecord(
                id="user_002", display_name="周宁", phone_masked="139****2048",
                role="CUSTOMER", status="ACTIVE", risk_level="LOW",
                created_at=current, updated_at=current,
            ),
        ])
        database.flush()
        database.add_all([
            AccountRecord(
                id="acc_001", user_id="user_001",
                payload={"currency": "CNY", "available_balance": balance},
            ),
            LifeGraphNodeRecord(
                node_id="event_001", owner_user_id="user_001", node_type="EVENT",
                display_label="爱人生日", sensitivity_level="SENSITIVE", status="ACTIVE",
                attributes={}, created_at=current, updated_at=current,
            ),
            LifeGraphNodeRecord(
                node_id="goal_001", owner_user_id="user_001", node_type="GOAL",
                display_label="生日礼物", sensitivity_level="PERSONAL", status="ACTIVE",
                attributes={}, created_at=current, updated_at=current,
            ),
        ])
        database.flush()
        database.add_all([
            LifeEventRecord(
                event_node_id="event_001", owner_user_id="user_001",
                event_type="BIRTHDAY", occurs_at=current + timedelta(days=2),
                timezone="Asia/Shanghai", recurrence_rule="FREQ=YEARLY",
                status=event_status, details={}, created_at=current, updated_at=current,
            ),
            FinancialGoalRecord(
                goal_node_id="goal_001", owner_user_id="user_001",
                target_amount=Decimal("1000.00"), reserved_amount=Decimal("200.00"),
                currency="CNY", due_at=current + timedelta(days=2),
                funding_policy={}, status="DRAFT", created_at=current, updated_at=current,
            ),
        ])
        database.flush()
        database.add(EventActionRecord(
            action_id="action_001", owner_user_id="user_001",
            event_node_id="event_001", goal_node_id="goal_001",
            action_type="REMIND_LIFE_EVENT", scheduled_at=current - timedelta(minutes=1),
            status=action_status, idempotency_key="birthday-reminder-001", payload={},
            attempt_count=0, max_attempts=max_attempts, delivery_receipt={},
            created_at=current, updated_at=current,
        ))
        if with_delivery_identity:
            database.add(ChannelIdentityRecord(
                identity_id="chid_001", user_id="user_001", channel="FEISHU",
                tenant_id="tenant_001", external_user_id_hash="hash_001",
                external_user_id_ciphertext=cipher.encrypt("ou_user_001"),
                status="ACTIVE", linked_at=current, created_at=current, updated_at=current,
            ))
    return cipher


def build_service(engine, cipher, sender, *, simulation_enabled=True, retry_seconds=10):
    return LifeEventRadarService(
        LifeEventRadarRepository(engine, retry_base_seconds=retry_seconds),
        ChannelGatewayRepository(engine),
        identity_cipher=cipher,
        sender=sender,
        trusted_app_base_url="http://127.0.0.1:5173",
        batch_size=20,
        lease_seconds=30,
        simulation_enabled=simulation_enabled,
    )


def test_claim_uses_lease_and_does_not_duplicate_between_workers():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    seed_radar(engine, now=now)
    repository = LifeEventRadarRepository(engine)

    first = repository.claim_due_actions(
        worker_id="worker-a", now=now, limit=10, lease_seconds=30
    )
    second = repository.claim_due_actions(
        worker_id="worker-b", now=now + timedelta(seconds=1), limit=10, lease_seconds=30
    )

    assert [item["action_id"] for item in first] == ["action_001"]
    assert second == []


def test_expired_lease_can_be_recovered_without_exceeding_attempt_limit():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    seed_radar(engine, now=now, max_attempts=2)
    repository = LifeEventRadarRepository(engine)
    repository.claim_due_actions(worker_id="worker-a", now=now, limit=1, lease_seconds=30)

    recovered = repository.claim_due_actions(
        worker_id="worker-b", now=now + timedelta(seconds=31), limit=1, lease_seconds=30
    )
    assert recovered[0]["attempt_count"] == 2
    assert recovered[0]["lease_owner"] == "worker-b"


def test_retry_keeps_provider_idempotency_key_and_success_is_not_resent():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now, max_attempts=2)
    sender = FakeSender(failures=1)
    service = build_service(engine, cipher, sender)

    first = service.scan_once(worker_id="worker-a", now=now)
    second = service.scan_once(worker_id="worker-b", now=now + timedelta(seconds=11))
    third = service.scan_once(worker_id="worker-c", now=now + timedelta(seconds=30))

    assert first["items"][0]["status"] == "FAILED"
    assert second["items"][0]["status"] == "SENT"
    assert third["claimed"] == 0
    assert [call["idempotency_key"] for call in sender.calls] == ["action_001", "action_001"]


def test_max_failures_enter_manual_review_instead_of_retrying_forever():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now, max_attempts=2)
    service = build_service(engine, cipher, FakeSender(failures=5))

    service.scan_once(worker_id="worker-a", now=now)
    result = service.scan_once(worker_id="worker-b", now=now + timedelta(seconds=11))
    later = service.scan_once(worker_id="worker-c", now=now + timedelta(days=1))

    assert result["items"][0]["status"] == "MANUAL_REVIEW"
    assert later["claimed"] == 0


def test_pause_resume_cancel_are_user_scoped_and_audited():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now)
    service = build_service(engine, cipher, FakeSender())

    paused = service.control_action(
        owner_user_id="user_001", action_id="action_001", command="PAUSE"
    )
    with pytest.raises(ApplicationError, match="SCHEDULED_ACTION_NOT_FOUND"):
        service.control_action(
            owner_user_id="user_002", action_id="action_001", command="RESUME"
        )
    resumed = service.control_action(
        owner_user_id="user_001", action_id="action_001", command="RESUME"
    )
    canceled = service.control_action(
        owner_user_id="user_001", action_id="action_001", command="CANCEL"
    )
    audits = service.list_audits("user_001", "action_001")

    assert (paused["status"], resumed["status"], canceled["status"]) == (
        "PAUSED", "PENDING", "CANCELED"
    )
    assert [item["event_type"] for item in audits] == [
        "USER_PAUSE", "USER_RESUME", "USER_CANCEL"
    ]


def test_delivery_reloads_latest_balance_and_uses_private_minimal_message():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now, balance="100.00")
    with Session(engine) as database, database.begin():
        database.get(AccountRecord, "acc_001").payload = {
            "currency": "CNY", "available_balance": "1200.00"
        }
    sender = FakeSender()
    service = build_service(engine, cipher, sender)

    result = service.scan_once(worker_id="worker-a", now=now)
    with Session(engine) as database:
        stored = database.get(EventActionRecord, "action_001")
        assert stored.delivery_receipt["funding_state"] == "AVAILABLE"

    assert result["sent"] == 1
    assert sender.calls[0]["receive_id"] == "ou_user_001"
    assert "爱人" not in sender.calls[0]["text"]
    assert "1000" not in sender.calls[0]["text"]
    assert "不能执行转账、扣款" in sender.calls[0]["text"]


def test_inactive_source_is_canceled_without_sending():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now, event_status="INACTIVE")
    sender = FakeSender()
    result = build_service(engine, cipher, sender).scan_once(
        worker_id="worker-a", now=now
    )

    assert result["items"][0]["status"] == "CANCELED"
    assert result["items"][0]["last_error_code"] == "LIFE_EVENT_INACTIVE"
    assert sender.calls == []


def test_simulation_is_explicit_and_can_be_disabled():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now)
    enabled = build_service(engine, cipher, FakeSender(), simulation_enabled=True)
    result = enabled.simulate_scan(owner_user_id="user_001", advance_days=30)

    assert result["clock"]["mode"] == "SANDBOX_SIMULATED_TIME"
    disabled = build_service(engine, cipher, FakeSender(), simulation_enabled=False)
    with pytest.raises(ApplicationError, match="LIFE_RADAR_SIMULATION_DISABLED"):
        disabled.simulate_scan(owner_user_id="user_001", advance_days=30)


def test_missing_delivery_identity_fails_with_stable_code_and_no_ciphertext_leak():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now, with_delivery_identity=False)
    service = build_service(engine, cipher, FakeSender())
    result = service.scan_once(worker_id="worker-a", now=now)

    assert result["items"][0]["last_error_code"] == "FEISHU_DELIVERY_ID_UNAVAILABLE"
    assert "ciphertext" not in str(service.list_actions("user_001")).lower()


def test_radar_api_requires_auth_and_returns_stable_cross_user_error():
    engine = build_engine()
    now = datetime.now(timezone.utc)
    cipher = seed_radar(engine, now=now)
    service = build_service(engine, cipher, FakeSender())
    app.dependency_overrides[get_life_event_radar_service] = lambda: service
    try:
        with TestClient(app) as client:
            app.dependency_overrides[get_current_auth] = lambda: auth_context("user_002")
            forbidden = client.post("/api/life-radar/actions/action_001/pause")
            app.dependency_overrides[get_current_auth] = lambda: auth_context("user_001")
            paused = client.post("/api/life-radar/actions/action_001/pause")
            audits = client.get("/api/life-radar/actions/action_001/audits")
    finally:
        app.dependency_overrides.clear()

    assert app.openapi()["paths"]["/api/life-radar/actions"]["get"]["security"] == [
        {"HTTPBearer": []}
    ]
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"]["code"] == "SCHEDULED_ACTION_NOT_FOUND"
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED"
    assert audits.json()["total"] == 1
