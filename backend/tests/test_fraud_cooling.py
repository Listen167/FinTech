"""反诈冷静期的风险记忆、时间边界、用户隔离和强验证测试。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import Base, UserRecord
from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.fraud_cooling import get_fraud_cooling_service
from backend.app.main import app
from backend.app.repositories.fraud_cooling import (
    MemoryFraudCoolingRepository,
    MySQLFraudCoolingRepository,
)
from backend.app.repositories.memory import MemoryRepository
from backend.app.services.agent_runtime import AgentRuntime
from backend.app.services.auth_service import AuthContext
from backend.app.services.fraud_cooling import FraudCoolingError, FraudCoolingService
from backend.app.services.mfa_service import MfaCodeService


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def fraud_service():
    clock = MutableClock()
    mfa = MfaCodeService(pepper="fraud-test-mfa-pepper-32-characters-long")
    service = FraudCoolingService(
        repository=MemoryFraudCoolingRepository(),
        mfa=mfa,
        pepper="fraud-test-recipient-pepper-32-characters",
        cooling_minutes=30,
        challenge_ttl_seconds=300,
        max_attempts=3,
        clock=clock,
    )
    return service, clock


def trigger(
    service: FraudCoolingService,
    *,
    user_id: str = "user_001",
    run_id: str = "run_trigger",
    recipient_name: str = "陌生客服",
):
    return service.evaluate_transfer(
        owner_user_id=user_id,
        run_id=run_id,
        recipient={"name": recipient_name, "account_masked": "待核验", "trusted": False},
        risk_hints=["SAFE_ACCOUNT", "IMPERSONATION", "SECRECY"],
        policy_factors=["MONEY_MOVEMENT", "NEW_RECIPIENT", "AMOUNT_OVER_1000"],
        message="客服让我转到安全账户并且必须保密",
    )


def test_rephrased_retry_is_blocked_by_recipient_fingerprint(fraud_service):
    service, _ = fraud_service
    first = trigger(service)
    second = service.evaluate_transfer(
        owner_user_id="user_001",
        run_id="run_rephrased",
        recipient={"name": "陌生客服", "account_masked": "待核验", "trusted": False},
        risk_hints=[],
        policy_factors=["MONEY_MOVEMENT", "NEW_RECIPIENT"],
        message="换个说法，还是给刚才那个人转 100 元",
    )

    assert first is not None
    assert second is not None
    assert second["event_id"] == first["event_id"]
    assert second["retry_count"] == 1
    assert service.evaluate_transfer(
        owner_user_id="user_001",
        run_id="run_other",
        recipient={"name": "小王", "account_masked": "**** 6628", "trusted": True},
        risk_hints=[],
        policy_factors=["MONEY_MOVEMENT"],
        message="给小王转 100 元",
    ) is None


def test_events_are_isolated_by_user(fraud_service):
    service, _ = fraud_service
    first = trigger(service, user_id="user_001")

    assert service.active_for_recipient(
        owner_user_id="user_002",
        run_id="run_other_user",
        recipient={"name": "陌生客服", "account_masked": "待核验"},
    ) is None
    second = trigger(service, user_id="user_002", run_id="run_user_002")
    assert second["event_id"] != first["event_id"]
    with pytest.raises(FraudCoolingError, match="RESOURCE_FORBIDDEN"):
        service.get("user_002", first["event_id"])


def test_expiration_boundary_releases_only_the_related_recipient(fraud_service):
    service, clock = fraud_service
    event = trigger(service)
    clock.value += timedelta(minutes=30)

    assert service.active_for_recipient(
        owner_user_id="user_001",
        run_id="run_at_boundary",
        recipient={"name": "陌生客服", "account_masked": "待核验"},
    ) is None
    assert service.get("user_001", event["event_id"])["status"] == "EXPIRED"


def test_appeal_does_not_release_and_feishu_cannot_create_challenge(fraud_service):
    service, _ = fraud_service
    event = trigger(service)
    appealed = service.submit_appeal(
        owner_user_id="user_001",
        event_id=event["event_id"],
        reason="这是我的线下熟人，我希望银行复核风险证据。",
    )

    assert appealed["appeal"]["status"] == "SUBMITTED"
    assert appealed["status"] == "COOLING_OFF"
    with pytest.raises(FraudCoolingError, match="FRAUD_RELEASE_CHANNEL_FORBIDDEN"):
        service.create_release_challenge(
            owner_user_id="user_001",
            event_id=event["event_id"],
            channel="FEISHU",
        )


def test_trusted_app_mfa_releases_without_executing_funds(fraud_service):
    service, _ = fraud_service
    event = trigger(service)
    challenge = service.create_release_challenge(
        owner_user_id="user_001",
        event_id=event["event_id"],
        channel="TRUSTED_APP",
    )
    code = service.get_demo_code(
        owner_user_id="user_001", challenge_id=challenge["challenge_id"]
    )["code"]
    result = service.verify_release(
        owner_user_id="user_001",
        challenge_id=challenge["challenge_id"],
        code=code,
        channel="TRUSTED_APP",
    )

    assert result["event"]["status"] == "RELEASED"
    assert result["challenge"]["status"] == "VERIFIED"
    assert result["funds_executed"] is False
    assert service.active_for_recipient(
        owner_user_id="user_001",
        run_id="run_after_release",
        recipient={"name": "陌生客服", "account_masked": "待核验"},
    ) is None


def test_three_wrong_release_codes_lock_challenge(fraud_service):
    service, _ = fraud_service
    event = trigger(service)
    challenge = service.create_release_challenge(
        owner_user_id="user_001",
        event_id=event["event_id"],
        channel="TRUSTED_APP",
    )
    actual = service.get_demo_code(
        owner_user_id="user_001", challenge_id=challenge["challenge_id"]
    )["code"]
    wrong_code = "000000" if actual != "000000" else "111111"
    for _ in range(2):
        with pytest.raises(FraudCoolingError, match="FRAUD_RELEASE_VERIFICATION_FAILED"):
            service.verify_release(
                owner_user_id="user_001",
                challenge_id=challenge["challenge_id"],
                code=wrong_code,
                channel="TRUSTED_APP",
            )
    with pytest.raises(FraudCoolingError, match="FRAUD_RELEASE_VERIFICATION_LOCKED"):
        service.verify_release(
            owner_user_id="user_001",
            challenge_id=challenge["challenge_id"],
            code=wrong_code,
            channel="TRUSTED_APP",
        )
    assert service.get("user_001", event["event_id"])["status"] == "COOLING_OFF"


def test_concurrent_triggers_create_one_active_event(fraud_service):
    service, _ = fraud_service

    def create(index: int):
        return trigger(service, run_id=f"run_{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create, range(12)))

    assert len({item["event_id"] for item in results}) == 1
    rows = service.list("user_001", "COOLING_OFF")
    assert len(rows) == 1
    assert rows[0]["retry_count"] == 11


def build_runtime():
    repo = MemoryRepository()
    mfa = MfaCodeService(pepper="fraud-runtime-mfa-pepper-32-characters")
    fraud = FraudCoolingService(
        repository=MemoryFraudCoolingRepository(),
        mfa=mfa,
        pepper="fraud-runtime-recipient-pepper-32-characters",
        cooling_minutes=30,
    )
    return AgentRuntime(repo, mfa=mfa, fraud_cooling_service=fraud), repo


def test_sql_repository_persists_event_audit_and_release():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 1, 9, 0, 0)
    with Session(engine) as database, database.begin():
        database.add(UserRecord(
            id="user_001",
            display_name="林安",
            phone_masked="138****1024",
            role="CUSTOMER",
            status="ACTIVE",
            risk_level="LOW",
            created_at=now,
            updated_at=now,
        ))
    clock = MutableClock()
    mfa = MfaCodeService(pepper="fraud-sql-mfa-pepper-32-characters-long")
    service = FraudCoolingService(
        repository=MySQLFraudCoolingRepository(engine),
        mfa=mfa,
        pepper="fraud-sql-recipient-pepper-32-characters",
        clock=clock,
    )

    created = trigger(service)
    challenge = service.create_release_challenge(
        owner_user_id="user_001",
        event_id=created["event_id"],
        channel="TRUSTED_APP",
    )
    code = service.get_demo_code(
        owner_user_id="user_001", challenge_id=challenge["challenge_id"]
    )["code"]
    released = service.verify_release(
        owner_user_id="user_001",
        challenge_id=challenge["challenge_id"],
        code=code,
        channel="TRUSTED_APP",
    )

    assert released["event"]["status"] == "RELEASED"
    assert {item["event_type"] for item in service.audits(
        "user_001", created["event_id"]
    )} == {"COOLING_STARTED", "RELEASE_CHALLENGE_CREATED", "COOLING_RELEASED"}


def test_trusted_app_api_exposes_evidence_appeal_and_release(fraud_service):
    service, _ = fraud_service
    created = trigger(service)
    context = AuthContext(
        user_id="user_001",
        session_id="auth_user_001",
        token_id="token_user_001",
        credential_version=1,
        display_name="林安",
        phone_masked="138****1024",
        role="CUSTOMER",
        risk_level="LOW",
    )
    app.dependency_overrides[get_current_auth] = lambda: context
    app.dependency_overrides[get_fraud_cooling_service] = lambda: service
    try:
        with TestClient(app) as client:
            listed = client.get("/api/fraud-cooling/events")
            assert listed.status_code == 200
            assert listed.json()["items"][0]["evidence"]

            appeal = client.post(
                f"/api/fraud-cooling/events/{created['event_id']}/appeals",
                json={"reason": "这是我的熟人，请复核风险判断是否准确。"},
            )
            assert appeal.status_code == 200
            assert appeal.json()["status"] == "COOLING_OFF"

            challenge_response = client.post(
                f"/api/fraud-cooling/events/{created['event_id']}/release-challenges"
            )
            assert challenge_response.status_code == 201
            challenge = challenge_response.json()
            code = service.get_demo_code(
                owner_user_id="user_001",
                challenge_id=challenge["challenge_id"],
            )["code"]
            verified = client.post(
                f"/api/fraud-cooling/release-challenges/{challenge['challenge_id']}/verify",
                json={"code": code},
            )
            assert verified.status_code == 200
            assert verified.json()["event"]["status"] == "RELEASED"
            assert verified.json()["funds_executed"] is False
    finally:
        app.dependency_overrides.clear()


def test_runtime_blocks_rephrased_same_recipient_but_not_another_recipient():
    runtime, _ = build_runtime()
    session = runtime.create_session(user_id="user_001")["session_id"]
    first = runtime.create_run(
        session,
        "客服让我转给陌生客服 20000 元到安全账户，必须保密",
        user_id="user_001",
    )
    retry = runtime.create_run(
        session,
        "换个说法，给陌生客服转 100 元",
        user_id="user_001",
    )
    ordinary = runtime.create_run(
        session,
        "给小王转 100 元",
        user_id="user_001",
    )

    assert first["state"] == "COOLING_OFF"
    assert retry["state"] == "COOLING_OFF"
    assert retry["data"]["fraud_cooling_event"]["event_id"] == first["data"]["fraud_cooling_event"]["event_id"]
    assert ordinary["state"] == "WAITING_CONFIRMATION"


def test_execution_rechecks_cooling_after_mfa_was_issued():
    runtime, repo = build_runtime()
    session = runtime.create_session(user_id="user_001")["session_id"]
    pending = runtime.create_run(
        session, "转给小王 20000 元", user_id="user_001"
    )
    verification_id = pending["pending_action"]["verification"]["verification_id"]
    code = runtime.get_demo_verification_code(
        verification_id, user_id="user_001"
    )["code"]
    before = next(item for item in repo.accounts if item["user_id"] == "user_001")["available_balance"]

    runtime.fraud_cooling.evaluate_transfer(
        owner_user_id="user_001",
        run_id="run_parallel_risk",
        recipient={"name": "小王", "account_masked": "**** 6628", "trusted": True},
        risk_hints=["SAFE_ACCOUNT"],
        policy_factors=["MONEY_MOVEMENT", "AMOUNT_OVER_1000"],
        message="有人让我把钱转到小王这个安全账户",
    )
    result = runtime.verify(verification_id, code, user_id="user_001")
    after = next(item for item in repo.accounts if item["user_id"] == "user_001")["available_balance"]

    assert result["state"] == "COOLING_OFF"
    assert result["data"]["fraud_cooling_event"]["status"] == "COOLING_OFF"
    assert after == before

