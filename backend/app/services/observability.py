"""比赛评测报告与不含敏感信息的运行聚合指标。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.models import ChannelMessageRecord
from backend.app.repositories.memory import MemoryRepository


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_REPORT_PATH = (
    Path(__file__).resolve().parents[3]
    / "ai"
    / "evals"
    / "reports"
    / "offline-baseline.v1.json"
)
PUBLIC_FAILURE_FIELDS = ("case_id", "category", "reason", "expected", "actual")
PUBLIC_METRIC_FIELDS = (
    "intent_accuracy_percent",
    "slot_completeness_percent",
    "fallback_rate_percent",
    "false_execution_rate_percent",
    "privilege_escalation_success_rate_percent",
    "p95_latency_ms",
)
PUBLIC_SECURITY_FIELDS = (
    "attack_cases",
    "high_risk_cases",
    "replay_cases",
    "false_execution_successes",
    "privilege_escalation_successes",
    "duplicate_execution_successes",
    "passed",
)


class ObservabilityError(ValueError):
    """评测报告缺失或格式错误。"""


class ObservabilityService:
    def __init__(
        self,
        repository: MemoryRepository,
        report_path: Path = DEFAULT_REPORT_PATH,
    ) -> None:
        self.repository = repository
        self.report_path = report_path

    def evaluation_summary(self) -> dict[str, Any]:
        try:
            report = json.loads(self.report_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ObservabilityError("EVALUATION_REPORT_NOT_FOUND") from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ObservabilityError("EVALUATION_REPORT_INVALID") from error

        required = {"schema_version", "evaluation_mode", "dataset", "versions", "metrics"}
        if not isinstance(report, dict) or not required.issubset(report):
            raise ObservabilityError("EVALUATION_REPORT_INVALID")

        failures = []
        for item in report.get("redacted_failures", [])[:12]:
            if isinstance(item, dict):
                failures.append({key: item.get(key) for key in PUBLIC_FAILURE_FIELDS})

        return {
            "schema_version": report["schema_version"],
            "generated_at": report.get("generated_at"),
            "evaluation_mode": report["evaluation_mode"],
            "dataset": self._public_dataset(report["dataset"]),
            "versions": self._public_versions(report["versions"]),
            "metrics": {
                key: report["metrics"].get(key) for key in PUBLIC_METRIC_FIELDS
            },
            "security_gate": {
                key: report.get("security_gate", {}).get(key)
                for key in PUBLIC_SECURITY_FIELDS
            },
            "redacted_failures": failures,
            "privacy": {
                "scope": "AGGREGATED_AND_REDACTED",
                "contains_raw_messages": False,
                "contains_user_identifiers": False,
                "contains_credentials": False,
            },
        }

    @staticmethod
    def _public_dataset(dataset: Any) -> dict[str, Any]:
        if not isinstance(dataset, dict):
            raise ObservabilityError("EVALUATION_REPORT_INVALID")
        categories = dataset.get("categories")
        return {
            "version": dataset.get("version"),
            "sha256": dataset.get("sha256"),
            "case_count": dataset.get("case_count"),
            "categories": categories if isinstance(categories, dict) else {},
        }

    @staticmethod
    def _public_versions(versions: Any) -> dict[str, Any]:
        if not isinstance(versions, dict):
            raise ObservabilityError("EVALUATION_REPORT_INVALID")
        model = versions.get("model") if isinstance(versions.get("model"), dict) else {}
        prompt = versions.get("prompt") if isinstance(versions.get("prompt"), dict) else {}
        policy = versions.get("policy") if isinstance(versions.get("policy"), dict) else {}
        return {
            "model": {
                "provider": model.get("provider"),
                "requested_model": model.get("requested_model"),
                "response_models": model.get("response_models", []),
                "invoked": bool(model.get("invoked")),
            },
            "prompt": {
                "version": prompt.get("version"),
                "sha256": prompt.get("sha256"),
            },
            "policy": {"version": policy.get("version")},
        }

    def runtime_metrics(self) -> dict[str, Any]:
        runs = list(self.repository.runs.values())
        run_states: Counter[str] = Counter()
        node_failure_codes: Counter[str] = Counter()
        model_fallbacks = 0
        for run in runs:
            run_states[str(run.get("state") or "UNKNOWN")] += 1
            trace = run.get("model_trace") or {}
            model_fallbacks += int(bool(trace.get("fallback_used")))
            for node in (run.get("plan") or {}).get("nodes", []):
                runtime = node.get("runtime") or {}
                if runtime.get("status") == "FAILED":
                    node_failure_codes[str(runtime.get("error_code") or "UNSPECIFIED")] += 1

        channel_metrics, channel_status = self._channel_metrics()
        total_runs = len(runs)
        return {
            "generated_at": datetime.now(BEIJING_TIMEZONE).isoformat(),
            "privacy_scope": "AGGREGATED_ONLY",
            "telemetry_status": channel_status,
            "totals": {
                "runs": total_runs,
                "failed_nodes": sum(node_failure_codes.values()),
                "model_fallbacks": model_fallbacks,
                "model_fallback_rate_percent": round(
                    model_fallbacks * 100 / total_runs, 2
                ) if total_runs else 0.0,
                "channel_failures": channel_metrics["failed"],
                "channel_rejections": channel_metrics["rejected"],
                "circuit_breakers": run_states["BLOCKED"] + run_states["COOLING_OFF"],
            },
            "run_states": [
                {"state": state, "count": count}
                for state, count in sorted(run_states.items())
            ],
            "node_failure_codes": [
                {"code": code, "count": count}
                for code, count in sorted(node_failure_codes.items())
            ],
            "definitions": {
                "circuit_breakers": "BLOCKED 与 COOLING_OFF Run 的聚合数量。",
                "channel_failures": "Channel Gateway 状态为 FAILED 的消息数量。",
                "model_fallbacks": "模型轨迹中 fallback_used=true 的 Run 数量。",
            },
        }

    def _channel_metrics(self) -> tuple[dict[str, int], str]:
        engine = getattr(self.repository, "engine", None)
        if engine is None:
            return {"failed": 0, "rejected": 0}, "MEMORY_ONLY"
        try:
            with Session(engine) as database:
                rows = database.execute(
                    select(ChannelMessageRecord.status, func.count())
                    .where(ChannelMessageRecord.status.in_(("FAILED", "REJECTED")))
                    .group_by(ChannelMessageRecord.status)
                ).all()
        except SQLAlchemyError:
            return {"failed": 0, "rejected": 0}, "DEGRADED"
        counts = {str(status): int(count) for status, count in rows}
        return {
            "failed": counts.get("FAILED", 0),
            "rejected": counts.get("REJECTED", 0),
        }, "AVAILABLE"
