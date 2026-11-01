"""兼容现有状态机字典接口的 MySQL 持久化仓库。"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from threading import RLock

from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AccountRecord,
    AgentRunRecord,
    AgentSessionRecord,
    AuditLogRecord,
    ConfirmationRecord,
    ContactRecord,
    IdempotencyRecord,
    SubscriptionRecord,
    TransactionRecord,
    TransferPreviewRecord,
    VerificationRecord,
)
from backend.app.repositories.memory import MemoryRepository


ALL_MODELS = (
    IdempotencyRecord,
    AuditLogRecord,
    VerificationRecord,
    ConfirmationRecord,
    TransferPreviewRecord,
    AgentRunRecord,
    AgentSessionRecord,
    TransactionRecord,
    SubscriptionRecord,
    ContactRecord,
    AccountRecord,
)


class MySQLRepository(MemoryRepository):
    backend = "mysql"
    persistent = True

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sync_lock = RLock()
        MemoryRepository.reset(self)
        if self._has_persisted_state():
            self._load()
        else:
            self.flush()

    def _has_persisted_state(self) -> bool:
        with Session(self.engine) as session:
            return bool(session.scalar(select(func.count()).select_from(AccountRecord)))

    def _load(self) -> None:
        def owned_payload(row) -> dict:
            payload = deepcopy(row.payload)
            payload.setdefault("user_id", row.user_id)
            return payload

        with self._sync_lock, Session(self.engine) as session:
            self.accounts = [owned_payload(row) for row in session.scalars(select(AccountRecord).order_by(AccountRecord.id))]
            self.contacts = [owned_payload(row) for row in session.scalars(select(ContactRecord).order_by(ContactRecord.id))]
            self.subscriptions = [owned_payload(row) for row in session.scalars(select(SubscriptionRecord).order_by(SubscriptionRecord.id))]
            self.transactions = [
                owned_payload(row)
                for row in session.scalars(select(TransactionRecord).order_by(TransactionRecord.sort_order))
            ]
            self.sessions = {
                row.session_id: owned_payload(row)
                for row in session.scalars(select(AgentSessionRecord))
            }
            self.runs = {
                row.run_id: owned_payload(row)
                for row in session.scalars(select(AgentRunRecord))
            }
            self.confirmations = {
                row.confirmation_id: owned_payload(row)
                for row in session.scalars(select(ConfirmationRecord))
            }
            self.verifications = {
                row.verification_id: owned_payload(row)
                for row in session.scalars(select(VerificationRecord))
            }
            self.previews = {
                row.preview_id: owned_payload(row)
                for row in session.scalars(select(TransferPreviewRecord))
            }
            self.audit_logs = [
                owned_payload(row)
                for row in session.scalars(select(AuditLogRecord).order_by(AuditLogRecord.sort_order))
            ]
            self.idempotency = {
                (row.user_id, row.client_key): row.run_id
                for row in session.scalars(select(IdempotencyRecord))
            }

    def reset(self) -> None:
        MemoryRepository.reset(self)
        self.flush()

    def flush(self) -> None:
        with self._sync_lock, Session(self.engine) as session, session.begin():
            for model in ALL_MODELS:
                session.execute(delete(model))

            session.add_all(
                AccountRecord(id=item["id"], user_id=item["user_id"], payload=deepcopy(item))
                for item in self.accounts
            )
            session.add_all(
                ContactRecord(id=item["id"], user_id=item["user_id"], payload=deepcopy(item))
                for item in self.contacts
            )
            session.add_all(
                SubscriptionRecord(
                    id=item["id"], user_id=item["user_id"], status=item["status"], payload=deepcopy(item)
                )
                for item in self.subscriptions
            )
            session.add_all(
                TransactionRecord(
                    id=item["id"], user_id=item["user_id"], sort_order=index, payload=deepcopy(item)
                )
                for index, item in enumerate(self.transactions)
            )
            session.add_all(
                AgentSessionRecord(
                    session_id=session_id,
                    user_id=item["user_id"],
                    updated_at=item["updated_at"],
                    payload=deepcopy(item),
                )
                for session_id, item in self.sessions.items()
            )
            session.add_all(
                AgentRunRecord(
                    run_id=run_id,
                    user_id=item["user_id"],
                    session_id=item["session_id"],
                    state=item["state"],
                    created_at=item["created_at"],
                    payload=deepcopy(item),
                )
                for run_id, item in self.runs.items()
            )
            session.add_all(
                ConfirmationRecord(
                    confirmation_id=confirmation_id,
                    user_id=item["user_id"],
                    run_id=item["run_id"],
                    status=item["status"],
                    payload=deepcopy(item),
                )
                for confirmation_id, item in self.confirmations.items()
            )
            session.add_all(
                VerificationRecord(
                    verification_id=verification_id,
                    user_id=item["user_id"],
                    run_id=item["run_id"],
                    status=item["status"],
                    payload=deepcopy(item),
                )
                for verification_id, item in self.verifications.items()
            )
            session.add_all(
                TransferPreviewRecord(
                    preview_id=preview_id,
                    user_id=item["user_id"],
                    status=item["status"],
                    payload=deepcopy(item),
                )
                for preview_id, item in self.previews.items()
            )
            session.add_all(
                AuditLogRecord(
                    audit_id=item["audit_id"],
                    user_id=item["user_id"],
                    run_id=item["run_id"],
                    action=item["action"],
                    result=item["result"],
                    created_at=item["created_at"],
                    sort_order=index,
                    payload=deepcopy(item),
                )
                for index, item in enumerate(self.audit_logs)
            )
            session.add_all(
                IdempotencyRecord(
                    idempotency_key=hashlib.sha256(f"{user_id}:{client_key}".encode("utf-8")).hexdigest(),
                    user_id=user_id,
                    client_key=client_key,
                    run_id=run_id,
                )
                for (user_id, client_key), run_id in self.idempotency.items()
            )

    def persistence_status(self) -> dict:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            connected = True
        except Exception:
            connected = False
        return {
            "backend": self.backend,
            "requested_backend": self.backend,
            "persistent": self.persistent,
            "connected": connected,
        }
