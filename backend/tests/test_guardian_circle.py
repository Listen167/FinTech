"""家庭守护圈的最小授权、双方确认、撤销和隐私边界测试。"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import Base, GuardianInvitationRecord, UserRecord
from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.guardian_circle import get_guardian_circle_service
from backend.app.main import app
from backend.app.schemas.guardian_circle import (
    CreateGuardianInvitationRequest,
    CreateGuardianRiskReviewRequest,
)
from backend.app.services.auth_service import AuthContext
from backend.app.services.guardian_circle import GuardianCircleError, GuardianCircleService


NOW = datetime(2026, 11, 1, 9, 0, 0)


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


def build_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as database, database.begin():
        database.add_all([
            UserRecord(
                id="user_001", display_name="林安", phone_masked="138****1024",
                role="CUSTOMER", status="ACTIVE", risk_level="LOW",
                created_at=NOW, updated_at=NOW,
            ),
            UserRecord(
                id="user_002", display_name="周宁", phone_masked="139****2048",
                role="CUSTOMER", status="ACTIVE", risk_level="LOW",
                created_at=NOW, updated_at=NOW,
            ),
            UserRecord(
                id="user_003", display_name="陈悦", phone_masked="136****3066",
                role="CUSTOMER", status="ACTIVE", risk_level="LOW",
                created_at=NOW, updated_at=NOW,
            ),
        ])
    return engine


def auth_context(user_id: str) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        session_id=f"session_{user_id}",
        token_id=f"token_{user_id}",
        credential_version=1,
        display_name={"user_001": "林安", "user_002": "周宁", "user_003": "陈悦"}[user_id],
        phone_masked="138****0000",
        role="CUSTOMER",
        risk_level="LOW",
    )


def invitation_payload(**changes) -> CreateGuardianInvitationRequest:
    values = {
        "guardian_user_id": "user_002",
        "scenarios": ["LARGE_TRANSFER", "NEW_RECIPIENT", "SUSPECTED_FRAUD"],
        "amount_lower_bound": "1000",
        "amount_upper_bound": "50000",
        "visible_fields": [
            "RISK_LEVEL", "SCENARIO", "AMOUNT_BAND", "RECIPIENT_MASKED",
            "RISK_SUMMARY", "OCCURRED_AT",
        ],
        "consent_valid_until": NOW + timedelta(days=30),
    }
    values.update(changes)
    return CreateGuardianInvitationRequest(**values)


def activate(service: GuardianCircleService):
    invitation = service.create_invitation(
        owner_user_id="user_001", payload=invitation_payload(), now=NOW
    )
    accepted = service.accept_invitation(
        guardian_user_id="user_002",
        token=invitation["invite_token"],
        now=NOW + timedelta(seconds=1),
    )
    return invitation, accepted["consent"]


def review_payload(consent_id: str, **changes) -> CreateGuardianRiskReviewRequest:
    values = {
        "consent_id": consent_id,
        "scenario": "NEW_RECIPIENT",
        "amount": "12000",
        "risk_level": "HIGH",
        "recipient_masked": "陌***户 · **** 6628",
        "risk_summary": "新收款人且金额较高，请共同核实是否认识对方。",
        "expires_in_seconds": 900,
    }
    values.update(changes)
    return CreateGuardianRiskReviewRequest(**values)


def test_invitation_requires_both_users_and_raw_token_is_not_persisted():
    engine = build_engine()
    service = GuardianCircleService(engine)
    invitation = service.create_invitation(
        owner_user_id="user_001", payload=invitation_payload(), now=NOW
    )

    assert invitation["status"] == "PENDING"
    assert invitation["invite_token"]
    assert invitation["token_returned_once"] is True
    assert service.list_consents("user_001", now=NOW) == []
    with Session(engine) as database:
        stored = database.scalar(select(GuardianInvitationRecord))
        assert stored.token_hash != invitation["invite_token"]
        assert len(stored.token_hash) == 64

    with pytest.raises(GuardianCircleError, match="RESOURCE_FORBIDDEN"):
        service.accept_invitation(
            guardian_user_id="user_003",
            token=invitation["invite_token"],
            now=NOW + timedelta(seconds=1),
        )

    accepted = service.accept_invitation(
        guardian_user_id="user_002",
        token=invitation["invite_token"],
        now=NOW + timedelta(seconds=1),
    )
    assert accepted["consent"]["status"] == "ACTIVE"
    assert accepted["funds_execution_allowed"] is False
    with pytest.raises(GuardianCircleError, match="GUARDIAN_INVITATION_REPLAYED"):
        service.accept_invitation(
            guardian_user_id="user_002",
            token=invitation["invite_token"],
            now=NOW + timedelta(seconds=2),
        )


def test_guardian_receives_only_allowlisted_summary_and_never_exact_amount():
    engine = build_engine()
    service = GuardianCircleService(engine)
    _, consent = activate(service)
    created = service.create_risk_review(
        owner_user_id="user_001",
        payload=review_payload(consent["consent_id"]),
        now=NOW + timedelta(seconds=2),
    )
    owner = service.owner_decide(
        owner_user_id="user_001", review_id=created["review_id"],
        confirmed=True, note="我确认需要家人共同核实", now=NOW + timedelta(seconds=3),
    )
    guardian_view = service.get_risk_review(
        actor_user_id="user_002", review_id=created["review_id"],
        now=NOW + timedelta(seconds=3),
    )

    assert owner["status"] == "AWAITING_GUARDIAN"
    assert set(guardian_view["visible_payload"]) == {
        "risk_level", "scenario", "amount_band", "recipient_masked",
        "risk_summary", "occurred_at",
    }
    serialized = str(guardian_view)
    assert "12000" not in serialized
    assert "available_balance" not in serialized
    assert "transactions" not in serialized
    assert guardian_view["source_run_id"] is None


def test_guardian_cannot_skip_owner_and_acknowledgement_never_authorizes_funds():
    service = GuardianCircleService(build_engine())
    _, consent = activate(service)
    review = service.create_risk_review(
        owner_user_id="user_001",
        payload=review_payload(consent["consent_id"]),
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(
        GuardianCircleError, match="GUARDIAN_RISK_REVIEW_NOT_GUARDIAN_CONFIRMABLE"
    ):
        service.guardian_decide(
            guardian_user_id="user_002", review_id=review["review_id"],
            risk_acknowledged=True, note="尝试跳过本人", now=NOW + timedelta(seconds=3),
        )

    service.owner_decide(
        owner_user_id="user_001", review_id=review["review_id"],
        confirmed=True, note="本人确认", now=NOW + timedelta(seconds=3),
    )
    result = service.guardian_decide(
        guardian_user_id="user_002", review_id=review["review_id"],
        risk_acknowledged=True, note="风险已知，建议本人继续核实",
        now=NOW + timedelta(seconds=4),
    )

    assert result["status"] == "ACKNOWLEDGED"
    assert result["safety_boundary"] == {
        "risk_acknowledgement_only": True,
        "funds_executed": False,
        "mfa_satisfied": False,
        "execution_authorized": False,
    }


def test_scope_violation_and_unmasked_sensitive_recipient_are_rejected():
    service = GuardianCircleService(build_engine())
    invitation = service.create_invitation(
        owner_user_id="user_001",
        payload=invitation_payload(
            scenarios=["SUSPECTED_FRAUD"],
            amount_lower_bound="5000",
            amount_upper_bound="10000",
        ),
        now=NOW,
    )
    consent = service.accept_invitation(
        guardian_user_id="user_002", token=invitation["invite_token"],
        now=NOW + timedelta(seconds=1),
    )["consent"]

    with pytest.raises(GuardianCircleError, match="GUARDIAN_SCOPE_VIOLATION"):
        service.create_risk_review(
            owner_user_id="user_001",
            payload=review_payload(consent["consent_id"], scenario="NEW_RECIPIENT"),
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(GuardianCircleError, match="GUARDIAN_SENSITIVE_FIELD_FORBIDDEN"):
        service.create_risk_review(
            owner_user_id="user_001",
            payload=review_payload(
                consent["consent_id"], scenario="SUSPECTED_FRAUD",
                amount="8000", recipient_masked="完整账号 6222021234567890",
            ),
            now=NOW + timedelta(seconds=2),
        )


def test_revoke_is_immediate_and_cancels_pending_reviews_and_guardian_access():
    service = GuardianCircleService(build_engine())
    _, consent = activate(service)
    review = service.create_risk_review(
        owner_user_id="user_001", payload=review_payload(consent["consent_id"]),
        now=NOW + timedelta(seconds=2),
    )
    service.owner_decide(
        owner_user_id="user_001", review_id=review["review_id"], confirmed=True,
        note="本人确认", now=NOW + timedelta(seconds=3),
    )
    revoked = service.revoke_consent(
        actor_user_id="user_001", consent_id=consent["consent_id"],
        reason="不再需要家庭协助", now=NOW + timedelta(seconds=4),
    )

    assert revoked["status"] == "REVOKED"
    assert service.list_consents("user_002", now=NOW + timedelta(seconds=5)) == []
    assert service.list_risk_reviews("user_002", now=NOW + timedelta(seconds=5)) == []
    with pytest.raises(GuardianCircleError, match="GUARDIAN_CONSENT_NOT_ACTIVE"):
        service.get_risk_review(
            actor_user_id="user_002", review_id=review["review_id"],
            now=NOW + timedelta(seconds=5),
        )
    owner_view = service.get_risk_review(
        actor_user_id="user_001", review_id=review["review_id"],
        now=NOW + timedelta(seconds=5),
    )
    assert owner_view["status"] == "CANCELED"


def test_expired_invite_and_consent_fail_closed():
    clock = MutableClock()
    service = GuardianCircleService(build_engine(), invite_ttl_seconds=60, clock=clock)
    invitation = service.create_invitation(
        owner_user_id="user_001", payload=invitation_payload(), now=clock.value
    )
    clock.value += timedelta(seconds=60)
    with pytest.raises(GuardianCircleError, match="GUARDIAN_INVITATION_EXPIRED"):
        service.accept_invitation(
            guardian_user_id="user_002", token=invitation["invite_token"]
        )

    clock.value = NOW
    short = service.create_invitation(
        owner_user_id="user_001",
        payload=invitation_payload(consent_valid_until=NOW + timedelta(minutes=11)),
        now=NOW,
    )
    consent = service.accept_invitation(
        guardian_user_id="user_002", token=short["invite_token"],
        now=NOW + timedelta(seconds=1),
    )["consent"]
    clock.value = NOW + timedelta(minutes=11)
    assert service.list_consents("user_002") == []
    with pytest.raises(GuardianCircleError, match="GUARDIAN_CONSENT_NOT_ACTIVE"):
        service.create_risk_review(
            owner_user_id="user_001", payload=review_payload(consent["consent_id"])
        )


@pytest.fixture
def api_setup():
    service = GuardianCircleService(build_engine())
    active_user = {"id": "user_001"}
    app.dependency_overrides[get_guardian_circle_service] = lambda: service
    app.dependency_overrides[get_current_auth] = lambda: auth_context(active_user["id"])
    try:
        with TestClient(app) as client:
            yield client, service, active_user
    finally:
        app.dependency_overrides.clear()


def test_guardian_http_flow_and_stable_unauthorized_error(api_setup):
    client, _, active_user = api_setup
    created = client.post("/api/guardian-circle/invitations", json={
        "guardian_user_id": "user_002",
        "scenarios": ["NEW_RECIPIENT"],
        "amount_lower_bound": "1000",
        "amount_upper_bound": "50000",
        "visible_fields": ["RISK_LEVEL", "SCENARIO", "AMOUNT_BAND", "RISK_SUMMARY"],
        "consent_valid_until": (NOW + timedelta(days=30)).isoformat(),
    })
    assert created.status_code == 201
    token = created.json()["invite_token"]

    active_user["id"] = "user_003"
    forbidden = client.post(
        "/api/guardian-circle/invitations/accept", json={"token": token}
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "RESOURCE_FORBIDDEN"

    active_user["id"] = "user_002"
    accepted = client.post(
        "/api/guardian-circle/invitations/accept", json={"token": token}
    )
    assert accepted.status_code == 200
    assert accepted.json()["consent"]["viewer_role"] == "GUARDIAN"

    active_user["id"] = "user_001"
    consent_id = accepted.json()["consent"]["consent_id"]
    review = client.post("/api/guardian-circle/risk-reviews", json={
        "consent_id": consent_id,
        "scenario": "NEW_RECIPIENT",
        "amount": "12000",
        "risk_level": "HIGH",
        "recipient_masked": "陌***户",
        "risk_summary": "新收款人且金额较高，请共同核实。",
        "expires_in_seconds": 900,
    })
    assert review.status_code == 201
    assert review.json()["status"] == "AWAITING_OWNER"

