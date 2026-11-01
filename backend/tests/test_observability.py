import json

from fastapi.testclient import TestClient

from backend.app.dependencies.auth import get_auth_service, get_current_auth
from backend.app.dependencies.observability import get_observability_service
from backend.app.main import app
from backend.app.repositories.memory import MemoryRepository
from backend.app.services.auth_service import AuthContext
from backend.app.services.observability import ObservabilityService


def auth_context() -> AuthContext:
    return AuthContext(
        user_id="user_001",
        session_id="auth_test",
        token_id="token_test",
        credential_version=1,
        display_name="演示用户",
        phone_masked="138****0000",
        role="CUSTOMER",
        risk_level="LOW",
    )


def baseline_report() -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-11-01T20:00:00+08:00",
        "evaluation_mode": "OFFLINE_DETERMINISTIC",
        "dataset": {
            "version": "financial-agent-eval.v1",
            "sha256": "a" * 64,
            "case_count": 380,
            "categories": {"ATTACK": 100, "INTENT": 120},
            "raw_message": "secret-dataset-value",
        },
        "versions": {
            "model": {"provider": "deepseek", "requested_model": "deepseek-chat"},
            "prompt": {"version": "prompt-v1", "sha256": "b" * 64},
            "policy": {"version": "policy-v1"},
        },
        "metrics": {
            "intent_accuracy_percent": 98.0,
            "slot_completeness_percent": 97.0,
            "fallback_rate_percent": 100.0,
            "false_execution_rate_percent": 0.0,
            "privilege_escalation_success_rate_percent": 0.0,
            "p95_latency_ms": 1.2,
        },
        "security_gate": {"passed": True},
        "redacted_failures": [{
            "case_id": "eval_001",
            "category": "AMBIGUOUS",
            "reason": "SLOT_ASSERTION_MISMATCH",
            "expected": "EXPECTED_SLOT_CONTRACT",
            "actual": "SLOT_CONTRACT_NOT_MET",
            "message": "不应由接口返回的原始文本",
            "token": "secret",
        }],
    }


def test_observability_aggregates_runs_without_user_data(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(baseline_report(), ensure_ascii=False), encoding="utf-8")
    repository = MemoryRepository()
    repository.runs = {
        "run_1": {
            "user_id": "user_secret",
            "state": "BLOCKED",
            "user_message": "敏感原文",
            "model_trace": {"fallback_used": True},
            "plan": {"nodes": [{
                "runtime": {"status": "FAILED", "error_code": "TOOL_TIMEOUT"}
            }]},
        },
        "run_2": {
            "user_id": "user_other",
            "state": "SUCCEEDED",
            "model_trace": {"fallback_used": False},
            "plan": {"nodes": []},
        },
    }
    service = ObservabilityService(repository, path)

    summary = service.evaluation_summary()
    metrics = service.runtime_metrics()

    assert set(summary["redacted_failures"][0]) == {
        "case_id", "category", "reason", "expected", "actual"
    }
    assert "敏感原文" not in json.dumps(metrics, ensure_ascii=False)
    assert "user_secret" not in json.dumps(metrics, ensure_ascii=False)
    assert metrics["telemetry_status"] == "MEMORY_ONLY"
    assert metrics["totals"] == {
        "runs": 2,
        "failed_nodes": 1,
        "model_fallbacks": 1,
        "model_fallback_rate_percent": 50.0,
        "channel_failures": 0,
        "channel_rejections": 0,
        "circuit_breakers": 1,
    }


def test_evaluation_api_requires_auth_and_returns_only_public_projection(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(baseline_report(), ensure_ascii=False), encoding="utf-8")
    service = ObservabilityService(MemoryRepository(), path)
    app.dependency_overrides[get_observability_service] = lambda: service
    app.dependency_overrides[get_auth_service] = lambda: object()
    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/evaluation/summary")
            app.dependency_overrides[get_current_auth] = auth_context
            summary = client.get("/api/evaluation/summary")
            metrics = client.get("/api/evaluation/runtime-metrics")
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert summary.status_code == 200
    assert metrics.status_code == 200
    serialized = summary.text.lower()
    assert "不应由接口返回" not in serialized
    assert "secret" not in serialized
    assert summary.json()["privacy"]["scope"] == "AGGREGATED_AND_REDACTED"
