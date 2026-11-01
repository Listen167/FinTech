"""生活关系图仓库，使用短事务直接读写 MySQL。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import (
    EventActionRecord,
    FamilyFundingNegotiationRecord,
    FinancialGoalRecord,
    FundingConsentScopeRecord,
    LifeEventRecord,
    LifeGraphChangeRequestRecord,
    LifeGraphEdgeRecord,
    LifeGraphNodeRecord,
    utc_now,
)


class IdempotencyConflictError(ValueError):
    """同一用户复用了幂等键，但候选变更内容不一致。"""


@dataclass(frozen=True)
class NewLifeGraphChangeRequest:
    request_id: str
    owner_user_id: str
    idempotency_key: str
    summary: str
    change_set: dict[str, Any]
    expires_at: datetime
    source: str = "STRUCTURED_INPUT"
    source_run_id: str | None = None
    schema_version: int = 1
    created_at: datetime = field(default_factory=utc_now)


def hash_change_set(change_set: dict[str, Any], schema_version: int = 1) -> str:
    canonical = json.dumps(
        {"schema_version": schema_version, "change_set": change_set},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LifeGraphRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_graph(self, owner_user_id: str) -> dict[str, list[dict[str, Any]]]:
        with Session(self.engine) as database:
            nodes = database.scalars(
                select(LifeGraphNodeRecord)
                .where(LifeGraphNodeRecord.owner_user_id == owner_user_id)
                .order_by(LifeGraphNodeRecord.node_id)
            ).all()
            edges = database.scalars(
                select(LifeGraphEdgeRecord)
                .where(LifeGraphEdgeRecord.owner_user_id == owner_user_id)
                .order_by(LifeGraphEdgeRecord.edge_id)
            ).all()
            events = database.scalars(
                select(LifeEventRecord)
                .where(LifeEventRecord.owner_user_id == owner_user_id)
                .order_by(LifeEventRecord.occurs_at, LifeEventRecord.event_node_id)
            ).all()
            goals = database.scalars(
                select(FinancialGoalRecord)
                .where(FinancialGoalRecord.owner_user_id == owner_user_id)
                .order_by(FinancialGoalRecord.due_at, FinancialGoalRecord.goal_node_id)
            ).all()
            actions = database.scalars(
                select(EventActionRecord)
                .where(EventActionRecord.owner_user_id == owner_user_id)
                .order_by(EventActionRecord.scheduled_at, EventActionRecord.action_id)
            ).all()

            return {
                "nodes": [self._node_to_dict(row) for row in nodes],
                "edges": [self._edge_to_dict(row) for row in edges],
                "events": [self._event_to_dict(row) for row in events],
                "goals": [self._goal_to_dict(row) for row in goals],
                "actions": [self._action_to_dict(row) for row in actions],
            }

    def reset_owner(self, owner_user_id: str) -> dict[str, int]:
        models = (
            EventActionRecord,
            LifeGraphEdgeRecord,
            LifeEventRecord,
            FundingConsentScopeRecord,
            FamilyFundingNegotiationRecord,
            FinancialGoalRecord,
            LifeGraphNodeRecord,
            LifeGraphChangeRequestRecord,
        )
        deleted: dict[str, int] = {}
        with Session(self.engine) as database, database.begin():
            for model in models:
                result = database.execute(
                    delete(model).where(model.owner_user_id == owner_user_id)
                )
                deleted[model.__tablename__] = max(result.rowcount or 0, 0)
        return deleted

    def create_change_request(
        self, draft: NewLifeGraphChangeRequest
    ) -> dict[str, Any]:
        snapshot_hash = hash_change_set(draft.change_set, draft.schema_version)

        try:
            with Session(self.engine, expire_on_commit=False) as database, database.begin():
                existing = self._find_by_idempotency_key(
                    database, draft.owner_user_id, draft.idempotency_key
                )
                if existing is not None:
                    return self._idempotent_result(existing, snapshot_hash)

                row = LifeGraphChangeRequestRecord(
                    request_id=draft.request_id,
                    owner_user_id=draft.owner_user_id,
                    source_run_id=draft.source_run_id,
                    source=draft.source,
                    status="PENDING",
                    schema_version=draft.schema_version,
                    idempotency_key=draft.idempotency_key,
                    snapshot_hash=snapshot_hash,
                    summary=draft.summary,
                    change_set=deepcopy(draft.change_set),
                    decision_reason=None,
                    expires_at=draft.expires_at,
                    decided_at=None,
                    created_at=draft.created_at,
                    updated_at=draft.created_at,
                )
                database.add(row)
                database.flush()
                return {"created": True, "request": self._change_request_to_dict(row)}
        except IntegrityError:
            with Session(self.engine) as database:
                existing = self._find_by_idempotency_key(
                    database, draft.owner_user_id, draft.idempotency_key
                )
                if existing is None:
                    raise
                return self._idempotent_result(existing, snapshot_hash)

    def get_change_request(
        self, owner_user_id: str, request_id: str
    ) -> dict[str, Any] | None:
        with Session(self.engine) as database:
            row = database.scalar(
                select(LifeGraphChangeRequestRecord).where(
                    LifeGraphChangeRequestRecord.request_id == request_id,
                    LifeGraphChangeRequestRecord.owner_user_id == owner_user_id,
                )
            )
            return self._change_request_to_dict(row) if row is not None else None

    def list_change_requests(
        self, owner_user_id: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            statement = select(LifeGraphChangeRequestRecord).where(
                LifeGraphChangeRequestRecord.owner_user_id == owner_user_id
            )
            if status is not None:
                statement = statement.where(LifeGraphChangeRequestRecord.status == status)
            rows = database.scalars(
                statement.order_by(
                    LifeGraphChangeRequestRecord.created_at.desc(),
                    LifeGraphChangeRequestRecord.request_id,
                )
            ).all()
            return [self._change_request_to_dict(row) for row in rows]

    @staticmethod
    def _find_by_idempotency_key(
        database: Session, owner_user_id: str, idempotency_key: str
    ) -> LifeGraphChangeRequestRecord | None:
        return database.scalar(
            select(LifeGraphChangeRequestRecord).where(
                LifeGraphChangeRequestRecord.owner_user_id == owner_user_id,
                LifeGraphChangeRequestRecord.idempotency_key == idempotency_key,
            )
        )

    def _idempotent_result(
        self, existing: LifeGraphChangeRequestRecord, snapshot_hash: str
    ) -> dict[str, Any]:
        if existing.snapshot_hash != snapshot_hash:
            raise IdempotencyConflictError("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_CONTENT")
        return {"created": False, "request": self._change_request_to_dict(existing)}

    @staticmethod
    def _node_to_dict(row: LifeGraphNodeRecord) -> dict[str, Any]:
        return {
            "node_id": row.node_id,
            "node_type": row.node_type,
            "display_label": row.display_label,
            "sensitivity_level": row.sensitivity_level,
            "status": row.status,
            "attributes": deepcopy(row.attributes),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _edge_to_dict(row: LifeGraphEdgeRecord) -> dict[str, Any]:
        return {
            "edge_id": row.edge_id,
            "source_node_id": row.source_node_id,
            "target_node_id": row.target_node_id,
            "relation_type": row.relation_type,
            "consent_scope": deepcopy(row.consent_scope),
            "provenance": row.provenance,
            "confidence_bp": row.confidence_bp,
            "status": row.status,
            "valid_from": LifeGraphRepository._optional_datetime(row.valid_from),
            "valid_until": LifeGraphRepository._optional_datetime(row.valid_until),
            "attributes": deepcopy(row.attributes),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _event_to_dict(row: LifeEventRecord) -> dict[str, Any]:
        return {
            "event_node_id": row.event_node_id,
            "event_type": row.event_type,
            "occurs_at": row.occurs_at.isoformat(),
            "timezone": row.timezone,
            "recurrence_rule": row.recurrence_rule,
            "status": row.status,
            "details": deepcopy(row.details),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _goal_to_dict(row: FinancialGoalRecord) -> dict[str, Any]:
        return {
            "goal_node_id": row.goal_node_id,
            "target_amount": LifeGraphRepository._decimal_to_string(row.target_amount),
            "reserved_amount": LifeGraphRepository._decimal_to_string(row.reserved_amount),
            "currency": row.currency,
            "due_at": LifeGraphRepository._optional_datetime(row.due_at),
            "funding_policy": deepcopy(row.funding_policy),
            "status": row.status,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _action_to_dict(row: EventActionRecord) -> dict[str, Any]:
        receipt = dict(row.delivery_receipt or {})
        public_receipt = {
            key: receipt[key]
            for key in ("channel", "delivered_at", "provider_message_id", "status")
            if key in receipt
        }
        return {
            "action_id": row.action_id,
            "event_node_id": row.event_node_id,
            "goal_node_id": row.goal_node_id,
            "action_type": row.action_type,
            "scheduled_at": row.scheduled_at.isoformat(),
            "status": row.status,
            "idempotency_key": row.idempotency_key,
            "payload": deepcopy(row.payload),
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "next_attempt_at": LifeGraphRepository._optional_datetime(row.next_attempt_at),
            "sent_at": LifeGraphRepository._optional_datetime(row.sent_at),
            "failed_at": LifeGraphRepository._optional_datetime(row.failed_at),
            "paused_at": LifeGraphRepository._optional_datetime(row.paused_at),
            "canceled_at": LifeGraphRepository._optional_datetime(row.canceled_at),
            "last_error_code": row.last_error_code,
            "delivery_receipt": public_receipt,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _change_request_to_dict(row: LifeGraphChangeRequestRecord) -> dict[str, Any]:
        return {
            "request_id": row.request_id,
            "source_run_id": row.source_run_id,
            "source": row.source,
            "status": row.status,
            "schema_version": row.schema_version,
            "idempotency_key": row.idempotency_key,
            "snapshot_hash": row.snapshot_hash,
            "summary": row.summary,
            "change_set": deepcopy(row.change_set),
            "decision_reason": row.decision_reason,
            "expires_at": row.expires_at.isoformat(),
            "decided_at": LifeGraphRepository._optional_datetime(row.decided_at),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _optional_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _decimal_to_string(value: Decimal) -> str:
        return format(value, ".2f")
