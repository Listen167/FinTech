import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from backend.app.db.models import Base
from backend.app.dependencies.auth import get_current_auth
from backend.app.main import app
from backend.app.repositories.memory import MemoryRepository, repository
from backend.app.repositories.mysql import MySQLRepository
from backend.app.services.agent_runtime import AgentRuntime, runtime
from backend.app.services.auth_service import AuthContext
from backend.app.services.operation_passport_service import redact_sensitive


USER_ID = "user_001"


def build_runtime(repo=None):
    repo = repo or MemoryRepository()
    service = AgentRuntime(repo)
    session_id = service.create_session(user_id=USER_ID)["session_id"]
    return service, repo, session_id


def complete_small_transfer(service, session_id):
    waiting = service.create_run(
        session_id,
        "给女儿转 200 元生活费",
        user_id=USER_ID,
    )
    confirmation_id = waiting["pending_action"]["confirmation"]["confirmation_id"]
    completed = service.decide_confirmation(
        confirmation_id,
        True,
        user_id=USER_ID,
    )
    return completed


def auth_context(user_id=USER_ID):
    return AuthContext(
        user_id=user_id,
        session_id=f"auth_{user_id}",
        token_id=f"token_{user_id}",
        credential_version=1,
        display_name="测试用户",
        phone_masked="138****0000",
        role="CUSTOMER",
        risk_level="NORMAL",
    )


def test_confirmed_transfer_passport_has_three_evidence_layers_and_versions():
    service, _, session_id = build_runtime()
    completed = complete_small_transfer(service, session_id)

    passport = service.get_operation_passport(completed["run_id"], user_id=USER_ID)

    assert passport["request"]["raw_message"] == "给女儿转 200 元生活费"
    assert passport["request"]["structured_intent"]["name"] == "transfer.create"
    assert passport["versions"]["prompt"]["version"]
    assert len(passport["versions"]["prompt"]["digest"]) == 64
    assert passport["versions"]["planner"]
    assert passport["versions"]["executor"]
    assert passport["versions"]["policy"]
    assert passport["user_authorization"]["method"] == "CONFIRMATION"
    assert passport["user_authorization"]["status"] == "APPROVED"
    assert passport["operation_snapshot"]["verified"] is True
    assert passport["bank_sandbox_result"]["status"] == "SUCCEEDED"
    assert passport["bank_sandbox_result"]["success_claim_allowed"] is True
    assert passport["bank_sandbox_result"]["decisive_receipts"][0]["reference_id"]
    assert passport["integrity"]["audit_chain"]["status"] == "VERIFIED"
    assert all(item["beijing_time"].endswith("+08:00") for item in passport["audit_timeline"])
    assert passport["passport_digest"] == service.get_operation_passport(
        completed["run_id"], user_id=USER_ID
    )["passport_digest"]


def test_mfa_passport_is_redacted_and_records_strong_authorization():
    service, repo, session_id = build_runtime()
    waiting = service.create_run(session_id, "转给小王 20000 元", user_id=USER_ID)
    verification_id = waiting["pending_action"]["verification"]["verification_id"]
    raw_otp_hash = repo.verifications[verification_id]["otp_hash"]
    code = service.get_demo_verification_code(verification_id, user_id=USER_ID)["code"]
    completed = service.verify(verification_id, code, user_id=USER_ID)

    passport = service.get_operation_passport(completed["run_id"], user_id=USER_ID)
    serialized = json.dumps(passport, ensure_ascii=False)

    assert passport["user_authorization"]["method"] == "MFA"
    assert passport["user_authorization"]["status"] == "APPROVED"
    assert passport["user_authorization"]["decided_at"].endswith("+08:00")
    assert code not in serialized
    assert raw_otp_hash not in serialized
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized


def test_pending_authorization_never_claims_bank_success():
    service, _, session_id = build_runtime()
    waiting = service.create_run(
        session_id,
        "给女儿转 200 元生活费",
        user_id=USER_ID,
    )

    passport = service.get_operation_passport(waiting["run_id"], user_id=USER_ID)

    assert passport["user_authorization"]["status"] == "PENDING"
    assert passport["bank_sandbox_result"]["status"] == "PENDING_AUTHORIZATION"
    assert passport["bank_sandbox_result"]["success_claim_allowed"] is False
    assert not passport["bank_sandbox_result"]["decisive_receipts"]


def test_missing_decisive_tool_receipt_disables_success_claim():
    service, repo, session_id = build_runtime()
    completed = complete_small_transfer(service, session_id)
    stored = repo.runs[completed["run_id"]]
    execute_node = next(
        node for node in stored["plan"]["nodes"]
        if node.get("tool") == "transfer.execute"
    )
    execute_node["runtime"]["output_receipt"] = None

    passport = service.get_operation_passport(completed["run_id"], user_id=USER_ID)

    assert passport["bank_sandbox_result"]["status"] == "UNVERIFIED_NO_TOOL_RECEIPT"
    assert passport["bank_sandbox_result"]["success_claim_allowed"] is False


def test_audit_chain_detects_modified_record():
    service, repo, session_id = build_runtime()
    completed = complete_small_transfer(service, session_id)
    original = service.get_operation_passport(completed["run_id"], user_id=USER_ID)
    assert original["integrity"]["audit_chain"]["status"] == "VERIFIED"

    record = next(item for item in repo.audit_logs if item["run_id"] == completed["run_id"])
    record["details"]["transaction_id"] = "modified_transaction"
    modified = service.get_operation_passport(completed["run_id"], user_id=USER_ID)

    assert modified["integrity"]["audit_chain"]["status"] == "TAMPERED"
    assert modified["bank_sandbox_result"]["status"] == "UNVERIFIED_INTEGRITY"
    assert modified["bank_sandbox_result"]["success_claim_allowed"] is False
    assert "不代表记录绝对不可篡改" in modified["integrity"]["disclaimer"]


def test_free_text_phone_account_and_credentials_are_redacted():
    source = {
        "raw_message": "给13800138000转账，卡号6222021234567890",
        "access_token": "sensitive-token-value",
        "nested": {"otp_hash": "sensitive-otp-hash"},
    }

    redacted = redact_sensitive(source)

    assert redacted["raw_message"] == "给138****8000转账，卡号6222********7890"
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["nested"]["otp_hash"] == "[REDACTED]"


def test_passport_is_owner_scoped():
    service, _, session_id = build_runtime()
    completed = complete_small_transfer(service, session_id)

    with pytest.raises(PermissionError, match="RESOURCE_FORBIDDEN"):
        service.get_operation_passport(completed["run_id"], user_id="user_002")


def test_audit_chain_and_passport_survive_mysql_repository_reload(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    first_repository = MySQLRepository(engine)
    first_runtime = AgentRuntime(first_repository)
    session_id = first_runtime.create_session(user_id=USER_ID)["session_id"]
    completed = complete_small_transfer(first_runtime, session_id)
    first_passport = first_runtime.get_operation_passport(
        completed["run_id"], user_id=USER_ID
    )

    reloaded_repository = MySQLRepository(engine)
    reloaded_runtime = AgentRuntime(reloaded_repository)
    reloaded_passport = reloaded_runtime.get_operation_passport(
        completed["run_id"], user_id=USER_ID
    )

    assert reloaded_passport["integrity"]["audit_chain"]["status"] == "VERIFIED"
    assert reloaded_passport["passport_digest"] == first_passport["passport_digest"]


def test_export_api_requires_auth_and_returns_redacted_non_cacheable_json():
    repository.reset()
    session_id = runtime.create_session(user_id=USER_ID)["session_id"]
    completed = complete_small_transfer(runtime, session_id)
    run_id = completed["run_id"]
    try:
        with TestClient(app) as client:
            def reject_unauthenticated():
                raise HTTPException(status_code=401, detail="AUTH_REQUIRED")

            app.dependency_overrides[get_current_auth] = reject_unauthenticated
            unauthenticated = client.get(f"/api/operation-passports/runs/{run_id}")
            assert unauthenticated.status_code == 401

            app.dependency_overrides[get_current_auth] = lambda: auth_context()
            exported = client.get(
                f"/api/operation-passports/runs/{run_id}/export"
            )
            assert exported.status_code == 200
            assert exported.headers["cache-control"] == "no-store"
            assert "attachment" in exported.headers["content-disposition"]
            assert exported.json()["run_id"] == run_id
            payload = exported.text.lower()
            assert "otp_hash" not in payload
            assert "access_token" not in payload

            app.dependency_overrides[get_current_auth] = lambda: auth_context("user_002")
            forbidden = client.get(f"/api/operation-passports/runs/{run_id}")
            assert forbidden.status_code == 403
            assert forbidden.json()["detail"]["code"] == "RESOURCE_FORBIDDEN"
    finally:
        app.dependency_overrides.clear()
