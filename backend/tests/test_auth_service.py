from datetime import datetime, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import (
    AuthEventRecord,
    Base,
    PasswordCredentialRecord,
    UserRecord,
)
from backend.app.dependencies.auth import get_auth_service
from backend.app.main import app
from backend.app.repositories.memory import repository
from backend.app.security.passwords import PasswordManager
from backend.app.security.tokens import AccessTokenManager
from backend.app.services.auth_service import AuthenticationError, AuthService


PASSWORDS = {
    "user_001": "DemoLogin2026A",
    "user_002": "DemoLogin2026B",
}
JWT_SECRET = "test-only-jwt-secret-with-at-least-32-characters"


def build_auth_service() -> tuple[AuthService, object]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 27, 20, 0, 0)
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
    token_manager = AccessTokenManager(
        JWT_SECRET,
        "HS256",
        "safebank-agent",
        "safebank-web",
        10,
    )
    service = AuthService(engine, token_manager, max_failed_attempts=5, lock_minutes=10)
    service.initialize_demo_credentials(PASSWORDS)
    return service, engine


def test_passwords_are_argon2id_hashes_and_never_audited_in_plaintext():
    service, engine = build_auth_service()

    with Session(engine) as database:
        credentials = database.scalars(
            select(PasswordCredentialRecord).order_by(PasswordCredentialRecord.user_id)
        ).all()
        events = database.scalars(select(AuthEventRecord)).all()

    assert len(credentials) == 2
    assert all(item.password_hash.startswith("$argon2id$") for item in credentials)
    assert PasswordManager().verify(PASSWORDS["user_001"], credentials[0].password_hash)
    assert all(password not in str(events) for password in PASSWORDS.values())


def test_demo_password_policy_requires_only_letters_and_digits():
    manager = PasswordManager()

    assert manager.hash("bankdemo2026").startswith("$argon2id$")
    with pytest.raises(ValueError, match="字母和数字"):
        manager.hash("onlyletters")
    with pytest.raises(ValueError, match="字母和数字"):
        manager.hash("12345678")
    with pytest.raises(ValueError, match="8 至 128"):
        manager.hash("abc123")


def test_five_failures_lock_account_and_correct_password_stays_blocked():
    service, engine = build_auth_service()

    for _ in range(5):
        with pytest.raises(AuthenticationError):
            service.authenticate("lin_an", "WrongPassword!2026", "127.0.0.1", "pytest")

    with pytest.raises(AuthenticationError, match="ACCOUNT_TEMPORARILY_LOCKED"):
        service.authenticate("lin_an", PASSWORDS["user_001"], "127.0.0.1", "pytest")

    with Session(engine) as database:
        credential = database.get(PasswordCredentialRecord, "user_001")
        events = database.scalars(
            select(AuthEventRecord).where(AuthEventRecord.user_id == "user_001")
        ).all()
    assert credential.locked_until is not None
    assert credential.failed_attempts == 5
    assert any(event.result == "LOCKED" for event in events)


def test_access_token_has_required_claims_and_resolves_current_user():
    service, _ = build_auth_service()

    login = service.authenticate("lin_an", PASSWORDS["user_001"], "127.0.0.1", "pytest")
    payload = service.tokens.decode(login["access_token"])
    context = service.validate_access_token(login["access_token"], "127.0.0.1")

    assert payload["sub"] == "user_001"
    assert payload["token_type"] == "access"
    assert payload["exp"] - payload["iat"] == 600
    assert all(key in payload for key in ("sid", "jti", "iss", "aud", "nbf", "credential_version"))
    assert context.user_id == "user_001"


def test_wrong_issuer_and_token_type_are_rejected():
    service, _ = build_auth_service()
    now = datetime.now(timezone.utc)
    invalid_payload = {
        "sub": "user_001",
        "sid": "auths_fake",
        "jti": "jti_fake",
        "iss": "attacker",
        "aud": "safebank-web",
        "iat": now,
        "nbf": now,
        "exp": now.timestamp() + 600,
        "token_type": "refresh",
        "credential_version": 1,
    }
    token = jwt.encode(invalid_payload, JWT_SECRET, algorithm="HS256")

    with pytest.raises(AuthenticationError, match="INVALID_ACCESS_TOKEN"):
        service.validate_access_token(token, "127.0.0.1")


def test_logout_revokes_server_side_session():
    service, _ = build_auth_service()
    login = service.authenticate("zhou_ning", PASSWORDS["user_002"], "127.0.0.1", "pytest")
    context = service.validate_access_token(login["access_token"])

    service.logout(context)

    with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
        service.validate_access_token(login["access_token"])


def test_refresh_rotation_and_reuse_detection_revoke_entire_session():
    service, engine = build_auth_service()
    login = service.authenticate("lin_an", PASSWORDS["user_001"], "127.0.0.1", "pytest")
    original_refresh = login["_refresh_token"]
    original_csrf = login["_csrf_token"]

    refreshed = service.refresh_access_token(
        original_refresh, original_csrf, "127.0.0.1", "pytest"
    )

    assert refreshed["_refresh_token"] != original_refresh
    assert refreshed["_csrf_token"] != original_csrf
    assert service.validate_access_token(refreshed["access_token"]).user_id == "user_001"

    with pytest.raises(AuthenticationError, match="REFRESH_TOKEN_REUSED"):
        service.refresh_access_token(
            original_refresh, original_csrf, "127.0.0.1", "pytest"
        )
    with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
        service.validate_access_token(refreshed["access_token"])

    with Session(engine) as database:
        reuse_event = database.scalar(
            select(AuthEventRecord).where(
                AuthEventRecord.event_type == "REFRESH_REUSE_DETECTED"
            )
        )
    assert reuse_event is not None
    assert reuse_event.result == "BLOCKED"


def test_wrong_csrf_rejects_refresh_without_destroying_valid_token():
    service, _ = build_auth_service()
    login = service.authenticate("lin_an", PASSWORDS["user_001"], "127.0.0.1", "pytest")

    with pytest.raises(AuthenticationError, match="CSRF_TOKEN_MISMATCH"):
        service.refresh_access_token(
            login["_refresh_token"], "wrong-csrf-token", "127.0.0.1", "pytest"
        )

    refreshed = service.refresh_access_token(
        login["_refresh_token"], login["_csrf_token"], "127.0.0.1", "pytest"
    )
    assert service.validate_access_token(refreshed["access_token"]).user_id == "user_001"


def test_logout_all_revokes_every_session_for_user():
    service, _ = build_auth_service()
    first = service.authenticate("lin_an", PASSWORDS["user_001"], "127.0.0.1", "device-1")
    second = service.authenticate("lin_an", PASSWORDS["user_001"], "127.0.0.2", "device-2")
    context = service.validate_access_token(first["access_token"])

    assert service.logout_all(context) == 2

    with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
        service.validate_access_token(first["access_token"])
    with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
        service.validate_access_token(second["access_token"])


def test_auth_api_login_me_and_logout():
    service, _ = build_auth_service()
    app.dependency_overrides[get_auth_service] = lambda: service
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": "lin_an", "password": PASSWORDS["user_001"]},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            assert "httponly" in login.headers.get("set-cookie", "").lower()
            csrf_token = client.cookies.get("safebank_csrf")
            cookie_metadata = {
                cookie.name: (cookie.path, cookie.secure)
                for cookie in client.cookies.jar
            }
            assert cookie_metadata["safebank_refresh"] == ("/api/auth", False)

            refresh = client.post(
                "/api/auth/refresh",
                headers={"X-CSRF-Token": csrf_token},
            )
            assert refresh.status_code == 200, refresh.text
            token = refresh.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            me = client.get("/api/auth/me", headers=headers)
            assert me.status_code == 200
            assert me.json()["id"] == "user_001"

            logout = client.post("/api/auth/logout", headers=headers)
            assert logout.status_code == 200
            assert client.get("/api/auth/me", headers=headers).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_agent_api_requires_jwt_and_isolates_user_resources():
    service, _ = build_auth_service()
    app.dependency_overrides[get_auth_service] = lambda: service
    repository.reset()
    try:
        first_login = service.authenticate(
            "lin_an", PASSWORDS["user_001"], "127.0.0.1", "pytest-user-1"
        )
        second_login = service.authenticate(
            "zhou_ning", PASSWORDS["user_002"], "127.0.0.2", "pytest-user-2"
        )
        first_headers = {"Authorization": f"Bearer {first_login['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second_login['access_token']}"}

        with TestClient(app) as client:
            assert client.post("/api/agent/sessions").status_code == 401
            first_session_response = client.post(
                "/api/agent/sessions", headers=first_headers
            )
            assert first_session_response.status_code == 201
            first_session_id = first_session_response.json()["session_id"]
            payload = {"session_id": first_session_id, "message": "帮我分析本月账单"}
            assert client.post("/api/agent/runs", json=payload).status_code == 401
            forged_session = client.post(
                "/api/agent/runs",
                json={"session_id": "client_forged", "message": "帮我分析本月账单"},
                headers=first_headers,
            )
            assert forged_session.status_code == 404

            created = client.post(
                "/api/agent/runs", json=payload, headers=first_headers
            )
            assert created.status_code == 200
            first_run_id = created.json()["run_id"]

            assert client.get(
                f"/api/agent/runs/{first_run_id}", headers=second_headers
            ).status_code == 403
            assert client.get(
                f"/api/agent/sessions/{first_session_id}", headers=second_headers
            ).status_code == 403

            transfer = client.post(
                "/api/agent/runs",
                json={"session_id": first_session_id, "message": "给小王转 200 元"},
                headers=first_headers,
            )
            assert transfer.status_code == 200
            confirmation_id = transfer.json()["pending_action"]["confirmation"][
                "confirmation_id"
            ]
            forbidden_confirmation = client.post(
                f"/api/agent/confirmations/{confirmation_id}/decide",
                json={"confirmed": True},
                headers=second_headers,
            )
            assert forbidden_confirmation.status_code == 403

            red_transfer = client.post(
                "/api/agent/runs",
                json={"session_id": first_session_id, "message": "给小王转 20000 元"},
                headers=first_headers,
            )
            assert red_transfer.status_code == 200
            verification = red_transfer.json()["pending_action"]["verification"]
            assert "code" not in verification
            verification_id = verification["verification_id"]
            assert client.get(
                f"/api/demo/verifications/{verification_id}/code",
                headers=second_headers,
            ).status_code == 403
            demo_code_response = client.get(
                f"/api/demo/verifications/{verification_id}/code",
                headers=first_headers,
            )
            assert demo_code_response.status_code == 200
            assert demo_code_response.headers["cache-control"] == "no-store"
            issued_code = demo_code_response.json()["code"]
            assert len(issued_code) == 6 and issued_code.isdigit()
            verified = client.post(
                f"/api/agent/verifications/{verification_id}/verify",
                json={"code": issued_code},
                headers=first_headers,
            )
            assert verified.status_code == 200
            assert verified.json()["state"] == "SUCCEEDED"
            replay = client.post(
                f"/api/agent/verifications/{verification_id}/verify",
                json={"code": issued_code},
                headers=first_headers,
            )
            assert replay.status_code == 409

            second_session_response = client.post(
                "/api/agent/sessions", headers=second_headers
            )
            assert second_session_response.status_code == 201
            second_session_id = second_session_response.json()["session_id"]
            second_run = client.post(
                "/api/agent/runs",
                json={"session_id": second_session_id, "message": "帮我分析本月账单"},
                headers=second_headers,
            )
            assert second_run.status_code == 200
            second_run_id = second_run.json()["run_id"]

            first_audits = client.get("/api/audit-logs", headers=first_headers)
            second_audits = client.get("/api/audit-logs", headers=second_headers)
            assert first_audits.status_code == 200
            assert second_audits.status_code == 200
            assert first_audits.json()["items"]
            assert second_audits.json()["items"]
            assert all(
                item["user_id"] == "user_001" for item in first_audits.json()["items"]
            )
            assert all(
                item["user_id"] == "user_002" for item in second_audits.json()["items"]
            )
            assert first_run_id not in {
                item["run_id"] for item in second_audits.json()["items"]
            }
            assert second_run_id not in {
                item["run_id"] for item in first_audits.json()["items"]
            }

            first_sessions = client.get("/api/agent/sessions", headers=first_headers)
            second_sessions = client.get("/api/agent/sessions", headers=second_headers)
            assert first_sessions.status_code == 200
            assert second_sessions.status_code == 200
            assert {item["session_id"] for item in first_sessions.json()["items"]} == {
                first_session_id
            }
            assert {item["session_id"] for item in second_sessions.json()["items"]} == {
                second_session_id
            }

            assert client.post("/api/demo/reset").status_code == 401
            assert client.post(
                "/api/demo/reset", headers=first_headers
            ).status_code == 200
    finally:
        repository.reset()
        app.dependency_overrides.clear()
