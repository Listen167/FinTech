"""主动生活事件雷达的租约、状态机与审计仓库。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, and_, or_, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AccountRecord,
    EventActionAuditRecord,
    EventActionRecord,
    FinancialGoalRecord,
    LifeEventRecord,
    LifeGraphNodeRecord,
)
from backend.app.errors import ApplicationError


def utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


class LifeEventRadarRepository:
    CONTROLLABLE_STATUSES = {"PENDING", "FAILED", "PAUSED"}

    def __init__(self, engine: Engine, *, retry_base_seconds: int = 60) -> None:
        self.engine = engine
        self.retry_base_seconds = retry_base_seconds

    @staticmethod
    def _public_action(row: EventActionRecord) -> dict[str, Any]:
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
            "scheduled_at": iso_utc(row.scheduled_at),
            "status": row.status,
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "next_attempt_at": iso_utc(row.next_attempt_at),
            "sent_at": iso_utc(row.sent_at),
            "failed_at": iso_utc(row.failed_at),
            "paused_at": iso_utc(row.paused_at),
            "canceled_at": iso_utc(row.canceled_at),
            "last_error_code": row.last_error_code,
            "delivery_receipt": public_receipt,
            "created_at": iso_utc(row.created_at),
            "updated_at": iso_utc(row.updated_at),
        }

    @classmethod
    def _claimed_action(cls, row: EventActionRecord) -> dict[str, Any]:
        return {
            **cls._public_action(row),
            "owner_user_id": row.owner_user_id,
            "idempotency_key": row.idempotency_key,
            "lease_owner": row.lease_owner,
            "lease_expires_at": iso_utc(row.lease_expires_at),
        }

    @staticmethod
    def _audit_payload(row: EventActionAuditRecord) -> dict[str, Any]:
        return {
            "audit_id": row.audit_id,
            "action_id": row.action_id,
            "event_type": row.event_type,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "actor": row.actor,
            "details": deepcopy(row.details or {}),
            "created_at": iso_utc(row.created_at),
        }

    @staticmethod
    def _append_audit(
        database: Session,
        row: EventActionRecord,
        *,
        event_type: str,
        from_status: str | None,
        actor: str,
        details: dict[str, Any] | None = None,
        now: datetime,
    ) -> None:
        database.add(
            EventActionAuditRecord(
                audit_id=f"saa_{uuid4().hex}",
                action_id=row.action_id,
                owner_user_id=row.owner_user_id,
                event_type=event_type,
                from_status=from_status,
                to_status=row.status,
                actor=actor[:128],
                details=deepcopy(details or {}),
                created_at=now,
            )
        )

    def list_actions(self, owner_user_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            rows = database.scalars(
                select(EventActionRecord)
                .where(EventActionRecord.owner_user_id == owner_user_id)
                .order_by(EventActionRecord.scheduled_at, EventActionRecord.action_id)
            ).all()
            return [self._public_action(row) for row in rows]

    def get_action(self, owner_user_id: str, action_id: str) -> dict[str, Any]:
        with Session(self.engine) as database:
            row = database.scalar(
                select(EventActionRecord).where(
                    EventActionRecord.action_id == action_id,
                    EventActionRecord.owner_user_id == owner_user_id,
                )
            )
            if row is None:
                raise ApplicationError("SCHEDULED_ACTION_NOT_FOUND")
            return self._public_action(row)

    def list_audits(self, owner_user_id: str, action_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            exists = database.scalar(
                select(EventActionRecord.action_id).where(
                    EventActionRecord.action_id == action_id,
                    EventActionRecord.owner_user_id == owner_user_id,
                )
            )
            if exists is None:
                raise ApplicationError("SCHEDULED_ACTION_NOT_FOUND")
            rows = database.scalars(
                select(EventActionAuditRecord)
                .where(
                    EventActionAuditRecord.action_id == action_id,
                    EventActionAuditRecord.owner_user_id == owner_user_id,
                )
                .order_by(EventActionAuditRecord.created_at, EventActionAuditRecord.audit_id)
            ).all()
            return [self._audit_payload(row) for row in rows]

    def claim_due_actions(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        owner_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        current = utc_naive(now)
        lease_expires_at = current + timedelta(seconds=lease_seconds)
        claimed: list[dict[str, Any]] = []
        with Session(self.engine) as database, database.begin():
            exhausted = select(EventActionRecord).where(
                EventActionRecord.status == "PROCESSING",
                EventActionRecord.lease_expires_at <= current,
                EventActionRecord.attempt_count >= EventActionRecord.max_attempts,
            )
            if owner_user_id is not None:
                exhausted = exhausted.where(EventActionRecord.owner_user_id == owner_user_id)
            for row in database.scalars(exhausted.with_for_update()).all():
                previous = row.status
                row.status = "MANUAL_REVIEW"
                row.lease_owner = None
                row.lease_expires_at = None
                row.last_error_code = "SCHEDULED_ACTION_LEASE_LOST"
                row.updated_at = current
                self._append_audit(
                    database,
                    row,
                    event_type="LEASE_EXHAUSTED",
                    from_status=previous,
                    actor="SYSTEM",
                    details={"error_code": row.last_error_code},
                    now=current,
                )

            due = or_(
                and_(
                    EventActionRecord.status == "PENDING",
                    EventActionRecord.scheduled_at <= current,
                    or_(
                        EventActionRecord.next_attempt_at.is_(None),
                        EventActionRecord.next_attempt_at <= current,
                    ),
                ),
                and_(
                    EventActionRecord.status == "FAILED",
                    or_(
                        EventActionRecord.next_attempt_at.is_(None),
                        EventActionRecord.next_attempt_at <= current,
                    ),
                ),
                and_(
                    EventActionRecord.status == "PROCESSING",
                    EventActionRecord.lease_expires_at <= current,
                    EventActionRecord.attempt_count < EventActionRecord.max_attempts,
                ),
            )
            statement = select(EventActionRecord).where(
                due,
                EventActionRecord.attempt_count < EventActionRecord.max_attempts,
            )
            if owner_user_id is not None:
                statement = statement.where(EventActionRecord.owner_user_id == owner_user_id)
            rows = database.scalars(
                statement.order_by(EventActionRecord.scheduled_at, EventActionRecord.action_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                previous = row.status
                row.status = "PROCESSING"
                row.attempt_count += 1
                row.lease_owner = worker_id
                row.lease_expires_at = lease_expires_at
                row.claimed_at = current
                row.next_attempt_at = None
                row.updated_at = current
                self._append_audit(
                    database,
                    row,
                    event_type="CLAIMED",
                    from_status=previous,
                    actor=worker_id,
                    details={"attempt": row.attempt_count},
                    now=current,
                )
                claimed.append(self._claimed_action(row))
        return claimed

    def load_delivery_context(self, action_id: str, owner_user_id: str) -> dict[str, Any]:
        """每次投递前重新读取事件、目标和银行余额，不使用动作旧快照。"""

        with Session(self.engine) as database:
            action = database.scalar(
                select(EventActionRecord).where(
                    EventActionRecord.action_id == action_id,
                    EventActionRecord.owner_user_id == owner_user_id,
                )
            )
            if action is None:
                raise ApplicationError("SCHEDULED_ACTION_NOT_FOUND")
            event = database.get(LifeEventRecord, action.event_node_id) if action.event_node_id else None
            goal = database.get(FinancialGoalRecord, action.goal_node_id) if action.goal_node_id else None
            event_node = database.get(LifeGraphNodeRecord, action.event_node_id) if action.event_node_id else None
            goal_node = database.get(LifeGraphNodeRecord, action.goal_node_id) if action.goal_node_id else None

            available_balance = Decimal("0.00")
            for account in database.scalars(
                select(AccountRecord).where(AccountRecord.user_id == owner_user_id)
            ).all():
                payload = dict(account.payload or {})
                if payload.get("currency", "CNY") != "CNY":
                    continue
                try:
                    available_balance += Decimal(str(payload.get("available_balance", 0)))
                except (InvalidOperation, TypeError, ValueError):
                    continue

            return {
                "action_id": action.action_id,
                "owner_user_id": action.owner_user_id,
                "action_type": action.action_type,
                "event": None
                if event is None
                else {
                    "event_type": event.event_type,
                    "occurs_at": iso_utc(event.occurs_at),
                    "status": event.status,
                    "node_status": event_node.status if event_node else "MISSING",
                    "updated_at": iso_utc(event.updated_at),
                },
                "goal": None
                if goal is None
                else {
                    "target_amount": format(goal.target_amount, ".2f"),
                    "reserved_amount": format(goal.reserved_amount, ".2f"),
                    "due_at": iso_utc(goal.due_at),
                    "status": goal.status,
                    "node_status": goal_node.status if goal_node else "MISSING",
                    "updated_at": iso_utc(goal.updated_at),
                },
                "available_balance": format(available_balance, ".2f"),
            }

    def mark_sent(
        self,
        *,
        action_id: str,
        worker_id: str,
        receipt: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        current = utc_naive(now)
        with Session(self.engine) as database, database.begin():
            row = database.scalar(
                select(EventActionRecord)
                .where(EventActionRecord.action_id == action_id)
                .with_for_update()
            )
            self._require_lease(row, worker_id, current)
            previous = row.status
            row.status = "SENT"
            row.sent_at = current
            row.last_error_code = None
            row.delivery_receipt = deepcopy(receipt)
            row.lease_owner = None
            row.lease_expires_at = None
            row.updated_at = current
            self._append_audit(
                database,
                row,
                event_type="DELIVERED",
                from_status=previous,
                actor=worker_id,
                details={"channel": receipt.get("channel", "FEISHU")},
                now=current,
            )
            database.flush()
            return self._public_action(row)

    def mark_failed(
        self,
        *,
        action_id: str,
        worker_id: str,
        error_code: str,
        now: datetime,
    ) -> dict[str, Any]:
        current = utc_naive(now)
        with Session(self.engine) as database, database.begin():
            row = database.scalar(
                select(EventActionRecord)
                .where(EventActionRecord.action_id == action_id)
                .with_for_update()
            )
            self._require_lease(row, worker_id, current, allow_expired=True)
            previous = row.status
            row.failed_at = current
            row.last_error_code = error_code[:64]
            row.lease_owner = None
            row.lease_expires_at = None
            if row.attempt_count >= row.max_attempts:
                row.status = "MANUAL_REVIEW"
                row.next_attempt_at = None
                event_type = "MANUAL_REVIEW_REQUIRED"
            else:
                row.status = "FAILED"
                delay = self.retry_base_seconds * (2 ** max(row.attempt_count - 1, 0))
                row.next_attempt_at = current + timedelta(seconds=delay)
                event_type = "DELIVERY_FAILED"
            row.updated_at = current
            self._append_audit(
                database,
                row,
                event_type=event_type,
                from_status=previous,
                actor=worker_id,
                details={"error_code": row.last_error_code, "attempt": row.attempt_count},
                now=current,
            )
            database.flush()
            return self._public_action(row)

    def cancel_from_worker(
        self,
        *,
        action_id: str,
        worker_id: str,
        reason_code: str,
        now: datetime,
    ) -> dict[str, Any]:
        current = utc_naive(now)
        with Session(self.engine) as database, database.begin():
            row = database.scalar(
                select(EventActionRecord)
                .where(EventActionRecord.action_id == action_id)
                .with_for_update()
            )
            self._require_lease(row, worker_id, current, allow_expired=True)
            previous = row.status
            row.status = "CANCELED"
            row.canceled_at = current
            row.last_error_code = reason_code[:64]
            row.lease_owner = None
            row.lease_expires_at = None
            row.next_attempt_at = None
            row.updated_at = current
            self._append_audit(
                database,
                row,
                event_type="SOURCE_CANCELED",
                from_status=previous,
                actor=worker_id,
                details={"reason_code": reason_code},
                now=current,
            )
            database.flush()
            return self._public_action(row)

    def transition_user_action(
        self,
        *,
        owner_user_id: str,
        action_id: str,
        command: str,
        now: datetime,
    ) -> dict[str, Any]:
        current = utc_naive(now)
        with Session(self.engine) as database, database.begin():
            row = database.scalar(
                select(EventActionRecord)
                .where(
                    EventActionRecord.action_id == action_id,
                    EventActionRecord.owner_user_id == owner_user_id,
                )
                .with_for_update()
            )
            if row is None:
                raise ApplicationError("SCHEDULED_ACTION_NOT_FOUND")
            if row.status == "PROCESSING":
                raise ApplicationError("SCHEDULED_ACTION_IN_PROGRESS")
            previous = row.status
            event_type = command
            if command == "PAUSE" and row.status in {"PENDING", "FAILED"}:
                row.status = "PAUSED"
                row.paused_at = current
                row.next_attempt_at = None
            elif command == "RESUME" and row.status == "PAUSED":
                row.status = "PENDING"
                row.paused_at = None
                row.next_attempt_at = None
                row.last_error_code = None
            elif command == "CANCEL" and row.status in self.CONTROLLABLE_STATUSES:
                row.status = "CANCELED"
                row.canceled_at = current
                row.next_attempt_at = None
            else:
                raise ApplicationError("SCHEDULED_ACTION_NOT_CONTROLLABLE")
            row.updated_at = current
            self._append_audit(
                database,
                row,
                event_type=f"USER_{event_type}",
                from_status=previous,
                actor=f"USER:{owner_user_id}",
                now=current,
            )
            database.flush()
            return self._public_action(row)

    @staticmethod
    def _require_lease(
        row: EventActionRecord | None,
        worker_id: str,
        now: datetime,
        *,
        allow_expired: bool = False,
    ) -> None:
        if row is None:
            raise ApplicationError("SCHEDULED_ACTION_NOT_FOUND")
        if row.status != "PROCESSING" or row.lease_owner != worker_id:
            raise ApplicationError("SCHEDULED_ACTION_LEASE_LOST")
        if not allow_expired and row.lease_expires_at is not None and row.lease_expires_at < now:
            raise ApplicationError("SCHEDULED_ACTION_LEASE_LOST")
