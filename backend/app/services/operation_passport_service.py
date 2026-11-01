"""从持久化业务事实重建脱敏的操作护照。"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from ai.agent.llm_parser import PROMPT_DIGEST, PROMPT_VERSION
from ai.agent.planner import PLANNER_VERSION
from backend.app.services.audit_integrity import canonical_digest, verify_audit_chain
from backend.app.services.policy_engine import POLICY_VERSION
from backend.app.services.task_graph_executor import EXECUTOR_VERSION


PASSPORT_SCHEMA_VERSION = "1.0.0"
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
INTEGRITY_DISCLAIMER = (
    "摘要链用于检测记录生成后的异常修改；比赛沙箱未接入外部可信时间戳或不可变存储，"
    "因此不代表记录绝对不可篡改。"
)

_SECRET_KEYS = {
    "password",
    "password_hash",
    "otp",
    "otp_hash",
    "verification_code",
    "access_token",
    "refresh_token",
    "csrf_token",
    "api_key",
    "client_secret",
    "app_secret",
}
_JWT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_LONG_ACCOUNT_PATTERN = re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)")


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in _SECRET_KEYS
        or "otp" in normalized
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
    )


def _mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 7:
        return f"{digits[:3]}****{digits[-4:]}"
    return "***"


def _mask_identifier(value: str) -> str:
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def redact_sensitive(value: Any, *, key: str = "") -> Any:
    """递归生成默认脱敏视图，不修改仓库原始数据。"""

    if _is_secret_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact_sensitive(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item, key=key) for item in value]
    if isinstance(value, str):
        normalized = key.lower()
        if "phone" in normalized or "mobile" in normalized:
            return _mask_phone(value)
        if "account" in normalized and not normalized.endswith("status"):
            return _mask_identifier(value)
        if value.lower().startswith("bearer ") or _JWT_PATTERN.fullmatch(value):
            return "[REDACTED]"
        value = _PHONE_PATTERN.sub(lambda match: _mask_phone(match.group(0)), value)
        value = _LONG_ACCOUNT_PATTERN.sub(r"\1********\2", value)
    return value


def _beijing_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TIMEZONE).isoformat(timespec="seconds")


def _tool_receipts(plan: dict | None) -> list[dict]:
    receipts: list[dict] = []
    for node in (plan or {}).get("nodes", []):
        runtime = node.get("runtime", {})
        receipt = runtime.get("output_receipt")
        if runtime.get("status") != "SUCCEEDED" or not isinstance(receipt, dict):
            continue
        if receipt.get("kind") != "TOOL_RESULT":
            continue
        receipts.append({
            "node_id": node.get("id"),
            "tool": node.get("tool"),
            "kind": receipt.get("kind"),
            "summary": receipt.get("summary"),
            "reference_id": redact_sensitive(receipt.get("reference_id"), key="reference_id"),
            "output_digest": receipt.get("output_digest"),
            "finished_at": _beijing_time(runtime.get("finished_at")),
        })
    return receipts


def _required_result_tools(intent_name: str | None) -> set[str]:
    return {
        "bill.analyze": {"bill.analyze"},
        "subscription.list": {"subscription.list"},
        "subscription.cancel": {"subscription.cancel.execute"},
        "transfer.create": {"transfer.execute"},
        "finance.cross_scenario": {"bill.analyze", "subscription.list"},
        "life.event_plan": {"life_graph.change_request.decide"},
    }.get(intent_name or "", set())


class OperationPassportService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def get(self, run_id: str, *, user_id: str) -> dict:
        run = self.repository.runs.get(run_id)
        if run is None:
            raise KeyError("RUN_NOT_FOUND")
        if run.get("user_id") != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")

        audits = [
            item for item in self.repository.audit_logs
            if item.get("run_id") == run_id
        ]
        audits.sort(
            key=lambda item: (
                item.get("chain_index", -1),
                item.get("created_at", ""),
            )
        )
        confirmation = next(
            (
                item for item in self.repository.confirmations.values()
                if item.get("run_id") == run_id
            ),
            None,
        )
        verification = next(
            (
                item for item in self.repository.verifications.values()
                if item.get("run_id") == run_id
            ),
            None,
        )
        authorization = self._authorization(run, confirmation, verification)
        receipts = _tool_receipts(run.get("plan"))
        chain = verify_audit_chain(self.repository.audit_logs, run_id)
        snapshot = self._operation_snapshot(confirmation or verification)
        bank_result = self._bank_result(
            run,
            receipts,
            authorization,
            integrity_trusted=(
                chain.get("status") != "TAMPERED"
                and snapshot.get("verified") is not False
            ),
        )

        passport = {
            "schema_version": PASSPORT_SCHEMA_VERSION,
            "passport_id": f"passport_{run_id}",
            "run_id": run_id,
            "generated_at": datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds"),
            "request": {
                "submitted_at": _beijing_time(run.get("created_at")),
                "raw_message": redact_sensitive(
                    run.get("user_message", ""),
                    key="raw_message",
                ),
                "structured_intent": redact_sensitive(deepcopy(run.get("intent"))),
            },
            "versions": deepcopy(run.get("versions") or {}),
            "model_interpretation": {
                "evidence_type": "MODEL_INTERPRETATION",
                "status": "STRUCTURED" if run.get("intent") else "NOT_AVAILABLE",
                "parser": (run.get("intent") or {}).get("parser"),
                "confidence": (run.get("intent") or {}).get("confidence"),
                "model_trace": redact_sensitive(deepcopy(run.get("model_trace"))),
                "notice": "模型输出仅用于理解和规划，不代表用户授权或银行执行结果。",
            },
            "task_graph": redact_sensitive(deepcopy(run.get("plan"))),
            "risk_evidence": {
                "policy": redact_sensitive(deepcopy(run.get("policy"))),
                "input_safety": redact_sensitive(deepcopy(run.get("safety"))),
            },
            "user_authorization": authorization,
            "operation_snapshot": snapshot,
            "tool_receipts": receipts,
            "bank_sandbox_result": bank_result,
            "audit_timeline": [self._timeline_item(item) for item in audits],
            "integrity": {
                "audit_chain": chain,
                "snapshot_verified": snapshot.get("verified"),
                "claim": "DETECTS_ABNORMAL_MODIFICATION_ONLY",
                "disclaimer": INTEGRITY_DISCLAIMER,
            },
        }
        digest_source = deepcopy(passport)
        digest_source.pop("generated_at", None)
        passport["passport_digest"] = canonical_digest(digest_source)
        return passport

    @staticmethod
    def _authorization(
        run: dict,
        confirmation: dict | None,
        verification: dict | None,
    ) -> dict:
        record = verification or confirmation
        if record is not None:
            raw_status = record.get("status", "PENDING")
            approved = raw_status in {"USED", "VERIFIED"}
            method = "MFA" if verification is not None else "CONFIRMATION"
            status = "APPROVED" if approved else raw_status
            decided_at = record.get("verified_at") or record.get("decided_at")
            return {
                "evidence_type": "USER_AUTHORIZATION",
                "method": method,
                "status": status,
                "reference_id": record.get("verification_id") or record.get("confirmation_id"),
                "snapshot_digest": record.get("operation_snapshot_hash"),
                "decided_at": _beijing_time(decided_at),
                "notice": "用户授权仅覆盖绑定快照，不等同于银行已经执行。",
            }

        user_receipt = next(
            (
                node.get("runtime", {}).get("output_receipt")
                for node in (run.get("plan") or {}).get("nodes", [])
                if node.get("runtime", {}).get("output_receipt", {}).get("kind")
                == "USER_DECISION"
            ),
            None,
        )
        if user_receipt:
            return {
                "evidence_type": "USER_AUTHORIZATION",
                "method": "LIFE_GRAPH_CONFIRMATION",
                "status": "APPROVED",
                "reference_id": user_receipt.get("reference_id"),
                "snapshot_digest": user_receipt.get("output_digest"),
                "decided_at": None,
                "notice": "用户授权仅覆盖生活图谱候选，不代表资金操作授权。",
            }

        policy = run.get("policy") or {}
        if policy.get("level") == "GREEN":
            return {
                "evidence_type": "USER_AUTHORIZATION",
                "method": "NOT_REQUIRED_READ_ONLY",
                "status": "NOT_REQUIRED",
                "reference_id": None,
                "snapshot_digest": None,
                "decided_at": None,
                "notice": "纯查询操作依据绿色策略自动执行。",
            }
        return {
            "evidence_type": "USER_AUTHORIZATION",
            "method": "NONE",
            "status": "NOT_REQUESTED",
            "reference_id": None,
            "snapshot_digest": None,
            "decided_at": None,
            "notice": "未发现用户授权证据。",
        }

    @staticmethod
    def _operation_snapshot(record: dict | None) -> dict:
        if record is None:
            return {"digest": None, "verified": None, "view": None}
        action = deepcopy(record.get("action") or {})
        stored = record.get("operation_snapshot_hash")
        return {
            "digest": stored,
            "verified": bool(stored) and canonical_digest(action) == stored,
            "view": redact_sensitive(action),
        }

    @staticmethod
    def _bank_result(
        run: dict,
        receipts: list[dict],
        authorization: dict,
        *,
        integrity_trusted: bool,
    ) -> dict:
        intent_name = (run.get("intent") or {}).get("name")
        required = _required_result_tools(intent_name)
        decisive = [item for item in receipts if item.get("tool") in required]
        approval_required = intent_name in {
            "subscription.cancel",
            "transfer.create",
            "life.event_plan",
        }
        authorization_trusted = (
            not approval_required or authorization.get("status") == "APPROVED"
        )
        success_allowed = (
            run.get("state") == "SUCCEEDED"
            and bool(decisive)
            and authorization_trusted
            and integrity_trusted
        )
        if success_allowed:
            status = "SUCCEEDED"
        elif run.get("state") == "SUCCEEDED" and not integrity_trusted:
            status = "UNVERIFIED_INTEGRITY"
        elif run.get("state") in {"WAITING_CONFIRMATION", "WAITING_VERIFICATION"}:
            status = "PENDING_AUTHORIZATION"
        elif run.get("state") in {"BLOCKED", "ABORTED", "FAILED", "WAITING_HUMAN"}:
            status = "NOT_SUCCEEDED"
        elif run.get("state") == "SUCCEEDED":
            status = "UNVERIFIED_NO_TOOL_RECEIPT"
        else:
            status = "NOT_EXECUTED"
        return {
            "evidence_type": "BANK_SANDBOX_RESULT",
            "status": status,
            "success_claim_allowed": success_allowed,
            "authorization_status": authorization.get("status"),
            "decisive_receipts": decisive,
            "notice": (
                "仅在确定性任务图存在对应成功工具回执时声明银行沙箱成功；"
                "模型文本和用户授权均不能替代工具回执。"
            ),
        }

    @staticmethod
    def _timeline_item(item: dict) -> dict:
        return {
            "audit_id": item.get("audit_id"),
            "chain_index": item.get("chain_index"),
            "previous_digest": item.get("previous_digest"),
            "record_digest": item.get("record_digest"),
            "action": item.get("action"),
            "result": item.get("result"),
            "details": redact_sensitive(deepcopy(item.get("details") or {})),
            "beijing_time": _beijing_time(item.get("created_at")),
        }
