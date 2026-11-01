"""Run 级审计摘要链工具。

摘要链只能发现记录与生成时内容不一致，不能替代外部可信时间戳、数字签名或
不可变存储，因此对外只声明“可检测异常修改”。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


GENESIS_DIGEST = "GENESIS"
CHAIN_FIELDS = (
    "audit_id",
    "user_id",
    "run_id",
    "session_id",
    "action",
    "result",
    "details",
    "created_at",
    "chain_index",
    "previous_digest",
)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_record_digest(record: dict) -> str:
    return canonical_digest({field: record.get(field) for field in CHAIN_FIELDS})


def next_chain_link(audits: list[dict], run_id: str) -> tuple[int, str]:
    complete = [
        item
        for item in audits
        if item.get("run_id") == run_id
        and isinstance(item.get("chain_index"), int)
        and isinstance(item.get("record_digest"), str)
    ]
    if not complete:
        return 0, GENESIS_DIGEST
    latest = max(complete, key=lambda item: item["chain_index"])
    return latest["chain_index"] + 1, latest["record_digest"]


def verify_audit_chain(audits: list[dict], run_id: str) -> dict:
    records = [item for item in audits if item.get("run_id") == run_id]
    if not records:
        return {
            "status": "EMPTY",
            "record_count": 0,
            "verified_count": 0,
            "head_digest": None,
        }

    complete = [
        item
        for item in records
        if isinstance(item.get("chain_index"), int)
        and isinstance(item.get("previous_digest"), str)
        and isinstance(item.get("record_digest"), str)
    ]
    if not complete:
        return {
            "status": "LEGACY_UNVERIFIED",
            "record_count": len(records),
            "verified_count": 0,
            "head_digest": None,
        }

    ordered = sorted(complete, key=lambda item: item["chain_index"])
    expected_index = 0
    expected_previous = GENESIS_DIGEST
    for item in ordered:
        valid = (
            item["chain_index"] == expected_index
            and item["previous_digest"] == expected_previous
            and item["record_digest"] == audit_record_digest(item)
        )
        if not valid:
            return {
                "status": "TAMPERED",
                "record_count": len(records),
                "verified_count": expected_index,
                "head_digest": ordered[-1].get("record_digest"),
                "failed_audit_id": item.get("audit_id"),
            }
        expected_previous = item["record_digest"]
        expected_index += 1

    status = "VERIFIED" if len(complete) == len(records) else "PARTIAL_UNVERIFIED"
    return {
        "status": status,
        "record_count": len(records),
        "verified_count": len(complete),
        "head_digest": ordered[-1]["record_digest"],
    }
