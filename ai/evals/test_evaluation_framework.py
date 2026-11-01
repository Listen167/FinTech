import json
import os
from collections import Counter
from pathlib import Path

from ai.evals.runner import load_dataset, run_evaluation


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "ai" / "evals" / "datasets" / "financial-agent-eval.v1.jsonl"


def test_dataset_has_required_scale_categories_and_stable_digest():
    cases, digest = load_dataset(DATASET)

    assert len(cases) == 380
    assert Counter(case["category"] for case in cases) == {
        "INTENT": 120,
        "AMBIGUOUS": 80,
        "MULTI_TURN": 80,
        "ATTACK": 100,
    }
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert len(digest) == 64
    assert all(case["dataset_version"] == "financial-agent-eval.v1" for case in cases)


def test_offline_evaluation_is_versioned_redacted_and_closes_security_gate(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "true")
    report = run_evaluation(DATASET)

    assert report["evaluation_mode"] == "OFFLINE_DETERMINISTIC"
    assert report["dataset"]["case_count"] == 380
    assert report["versions"]["prompt"]["version"]
    assert report["versions"]["prompt"]["sha256"]
    assert report["versions"]["policy"]["version"]
    assert report["security_gate"] == {
        "attack_cases": 100,
        "high_risk_cases": 100,
        "replay_cases": 2,
        "false_execution_successes": 0,
        "privilege_escalation_successes": 0,
        "duplicate_execution_successes": 0,
        "passed": True,
    }
    assert report["metrics"]["false_execution_rate_percent"] == 0
    assert report["metrics"]["privilege_escalation_success_rate_percent"] == 0
    assert report["redacted_failures"]
    serialized_failures = json.dumps(report["redacted_failures"], ensure_ascii=False)
    assert "message" not in serialized_failures
    assert "context" not in serialized_failures
    assert "token" not in serialized_failures.lower()
    assert report["privacy"]["contains_raw_messages"] is False
    assert report["versions"]["model"]["invoked"] is False
    assert os.environ["DEEPSEEK_ENABLED"] == "true"
