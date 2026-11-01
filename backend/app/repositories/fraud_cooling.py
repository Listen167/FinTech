"""反诈冷静期仓库：活动事件唯一性、并发重试和完整审计。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import (
    FraudReleaseChallengeRecord,
    FraudRiskAuditRecord,
    FraudRiskEventRecord,
)


class FraudRepositoryError(ValueError):
    pass


@dataclass(frozen=True)
class NewFraudRiskEvent:
    event_id: str
    owner_user_id: str
    trigger_run_id: str | None
    recipient_fingerprint: str
    recipient_display: str
    recipient_account_masked: str | None
    signals: list[str]
    evidence: list[dict[str, Any]]
    policy_version: str
    message_digest: str
    triggered_at: datetime
    cooling_until: datetime


@dataclass(frozen=True)
class NewFraudReleaseChallenge:
    challenge_id: str
    event_id: str
    owner_user_id: str
    otp_hash: str
    issued_at: datetime
    expires_at: datetime


def _active_scope(owner_user_id: str, recipient_fingerprint: str) -> str:
    return f"{owner_user_id}:{recipient_fingerprint}"


def _new_audit(
    *, event_id: str, owner_user_id: str, event_type: str, actor: str,
    details: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    return {
        "audit_id": f"fraud_audit_{uuid4().hex[:16]}",
        "event_id": event_id,
        "owner_user_id": owner_user_id,
        "event_type": event_type,
        "actor": actor,
        "details": deepcopy(details),
        "created_at": now,
    }


class MemoryFraudCoolingRepository:
    """测试和无数据库模式使用的线程安全仓库。"""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.challenges: dict[str, dict[str, Any]] = {}
        self.audit_rows: list[dict[str, Any]] = []
        self._lock = RLock()

    def _audit(
        self, row: dict[str, Any], event_type: str, actor: str,
        details: dict[str, Any], now: datetime,
    ) -> None:
        self.audit_rows.append(_new_audit(
            event_id=row["event_id"], owner_user_id=row["owner_user_id"],
            event_type=event_type, actor=actor, details=details, now=now,
        ))

    def _expire_if_due(self, row: dict[str, Any], now: datetime) -> bool:
        if row["status"] != "COOLING_OFF" or now < row["cooling_until"]:
            return False
        row.update(status="EXPIRED", active_scope_key=None, updated_at=now)
        for challenge in self.challenges.values():
            if challenge["event_id"] == row["event_id"] and challenge["status"] == "PENDING":
                challenge.update(status="EXPIRED", updated_at=now)
        self._audit(row, "COOLING_EXPIRED", "SYSTEM", {}, now)
        return True

    def block_active_retry(
        self, owner_user_id: str, recipient_fingerprint: str,
        run_id: str | None, now: datetime,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = next((item for item in self.events.values() if (
                item["owner_user_id"] == owner_user_id
                and item["recipient_fingerprint"] == recipient_fingerprint
                and item["status"] == "COOLING_OFF"
            )), None)
            if row is None or self._expire_if_due(row, now):
                return None
            row["retry_count"] += 1
            row["updated_at"] = now
            self._audit(
                row, "RETRY_BLOCKED", "AGENT",
                {"run_id": run_id, "retry_count": row["retry_count"]}, now,
            )
            return deepcopy(row)

    def trigger_or_reuse(self, draft: NewFraudRiskEvent) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            existing = next((item for item in self.events.values() if (
                item["owner_user_id"] == draft.owner_user_id
                and item["recipient_fingerprint"] == draft.recipient_fingerprint
                and item["status"] == "COOLING_OFF"
            )), None)
            if existing is not None and not self._expire_if_due(existing, draft.triggered_at):
                existing["retry_count"] += 1
                existing["updated_at"] = draft.triggered_at
                self._audit(
                    existing, "RETRY_BLOCKED", "AGENT",
                    {"run_id": draft.trigger_run_id, "retry_count": existing["retry_count"]},
                    draft.triggered_at,
                )
                return False, deepcopy(existing)
            row = {
                **draft.__dict__,
                "active_scope_key": _active_scope(
                    draft.owner_user_id, draft.recipient_fingerprint
                ),
                "status": "COOLING_OFF",
                "retry_count": 0,
                "appeal_status": "NONE",
                "appeal_reason": None,
                "appeal_submitted_at": None,
                "released_at": None,
                "release_method": None,
                "created_at": draft.triggered_at,
                "updated_at": draft.triggered_at,
            }
            self.events[row["event_id"]] = row
            self._audit(
                row, "COOLING_STARTED", "POLICY_ENGINE",
                {"signals": row["signals"], "trigger_run_id": row["trigger_run_id"]},
                draft.triggered_at,
            )
            return True, deepcopy(row)

    def get(self, owner_user_id: str, event_id: str, now: datetime) -> dict[str, Any] | None:
        with self._lock:
            row = self.events.get(event_id)
            if row is None or row["owner_user_id"] != owner_user_id:
                return None
            self._expire_if_due(row, now)
            return deepcopy(row)

    def exists_for_other_owner(self, owner_user_id: str, event_id: str) -> bool:
        with self._lock:
            row = self.events.get(event_id)
            return row is not None and row["owner_user_id"] != owner_user_id

    def list(self, owner_user_id: str, status: str | None, now: datetime) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item for item in self.events.values() if item["owner_user_id"] == owner_user_id]
            for row in rows:
                self._expire_if_due(row, now)
            if status is not None:
                rows = [row for row in rows if row["status"] == status]
            rows.sort(key=lambda item: (item["triggered_at"], item["event_id"]), reverse=True)
            return deepcopy(rows)

    def audits(self, owner_user_id: str, event_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item for item in self.audit_rows if (
                item["owner_user_id"] == owner_user_id and item["event_id"] == event_id
            )]
            rows.sort(key=lambda item: (item["created_at"], item["audit_id"]))
            return deepcopy(rows)

    def submit_appeal(
        self, owner_user_id: str, event_id: str, reason: str, now: datetime,
    ) -> dict[str, Any]:
        with self._lock:
            row = self.events.get(event_id)
            if row is None or row["owner_user_id"] != owner_user_id:
                raise FraudRepositoryError("FRAUD_EVENT_NOT_FOUND")
            self._expire_if_due(row, now)
            if row["status"] != "COOLING_OFF":
                raise FraudRepositoryError("FRAUD_EVENT_NOT_ACTIVE")
            if row["appeal_status"] == "SUBMITTED":
                raise FraudRepositoryError("FRAUD_APPEAL_ALREADY_SUBMITTED")
            row.update(
                appeal_status="SUBMITTED", appeal_reason=reason,
                appeal_submitted_at=now, updated_at=now,
            )
            self._audit(row, "APPEAL_SUBMITTED", "USER", {"reason": reason}, now)
            return deepcopy(row)

    def create_challenge(self, draft: NewFraudReleaseChallenge) -> dict[str, Any]:
        with self._lock:
            event = self.events.get(draft.event_id)
            if event is None or event["owner_user_id"] != draft.owner_user_id:
                raise FraudRepositoryError("FRAUD_EVENT_NOT_FOUND")
            self._expire_if_due(event, draft.issued_at)
            if event["status"] != "COOLING_OFF":
                raise FraudRepositoryError("FRAUD_EVENT_NOT_ACTIVE")
            for challenge in self.challenges.values():
                if challenge["event_id"] == draft.event_id and challenge["status"] == "PENDING":
                    challenge.update(status="SUPERSEDED", updated_at=draft.issued_at)
            row = {
                **draft.__dict__,
                "status": "PENDING",
                "failed_attempts": 0,
                "delivery_channel": "SMS",
                "verified_at": None,
                "created_at": draft.issued_at,
                "updated_at": draft.issued_at,
            }
            self.challenges[row["challenge_id"]] = row
            self._audit(
                event, "RELEASE_CHALLENGE_CREATED", "TRUSTED_APP",
                {"challenge_id": row["challenge_id"]}, draft.issued_at,
            )
            return deepcopy(row)

    def get_challenge(
        self, owner_user_id: str, challenge_id: str, now: datetime,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.challenges.get(challenge_id)
            if row is None or row["owner_user_id"] != owner_user_id:
                return None
            if row["status"] == "PENDING" and now >= row["expires_at"]:
                row.update(status="EXPIRED", updated_at=now)
                event = self.events[row["event_id"]]
                self._audit(
                    event, "RELEASE_CHALLENGE_EXPIRED", "SYSTEM",
                    {"challenge_id": challenge_id}, now,
                )
            return deepcopy(row)

    def fail_challenge(
        self, owner_user_id: str, challenge_id: str, now: datetime, max_attempts: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            challenge = self.challenges.get(challenge_id)
            if challenge is None or challenge["owner_user_id"] != owner_user_id:
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_FOUND")
            if challenge["status"] == "PENDING" and now >= challenge["expires_at"]:
                challenge.update(status="EXPIRED", updated_at=now)
            if challenge["status"] != "PENDING":
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_PENDING")
            challenge["failed_attempts"] += 1
            if challenge["failed_attempts"] >= max_attempts:
                challenge["status"] = "LOCKED"
            challenge["updated_at"] = now
            event = self.events[challenge["event_id"]]
            self._audit(
                event, "RELEASE_VERIFICATION_FAILED", "TRUSTED_APP",
                {
                    "challenge_id": challenge_id,
                    "failed_attempts": challenge["failed_attempts"],
                    "locked": challenge["status"] == "LOCKED",
                },
                now,
            )
            return deepcopy(event), deepcopy(challenge)

    def release(
        self, owner_user_id: str, challenge_id: str, now: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            challenge = self.challenges.get(challenge_id)
            if challenge is None or challenge["owner_user_id"] != owner_user_id:
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_FOUND")
            if challenge["status"] == "PENDING" and now >= challenge["expires_at"]:
                challenge.update(status="EXPIRED", updated_at=now)
            if challenge["status"] != "PENDING":
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_PENDING")
            event = self.events[challenge["event_id"]]
            self._expire_if_due(event, now)
            if event["status"] != "COOLING_OFF":
                raise FraudRepositoryError("FRAUD_EVENT_NOT_ACTIVE")
            challenge.update(status="VERIFIED", verified_at=now, updated_at=now)
            event.update(
                status="RELEASED", active_scope_key=None, released_at=now,
                release_method="TRUSTED_APP_MFA", updated_at=now,
            )
            self._audit(
                event, "COOLING_RELEASED", "TRUSTED_APP",
                {"challenge_id": challenge_id, "method": "MFA"}, now,
            )
            return deepcopy(event), deepcopy(challenge)

    def reset_owner(self, owner_user_id: str) -> None:
        with self._lock:
            event_ids = {
                event_id for event_id, row in self.events.items()
                if row["owner_user_id"] == owner_user_id
            }
            self.events = {
                key: value for key, value in self.events.items() if key not in event_ids
            }
            self.challenges = {
                key: value for key, value in self.challenges.items()
                if value["event_id"] not in event_ids
            }
            self.audit_rows = [
                row for row in self.audit_rows if row["event_id"] not in event_ids
            ]


class MySQLFraudCoolingRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _audit(
        database: Session, row: FraudRiskEventRecord, event_type: str,
        actor: str, details: dict[str, Any], now: datetime,
    ) -> None:
        database.add(FraudRiskAuditRecord(**_new_audit(
            event_id=row.event_id,
            owner_user_id=row.owner_user_id,
            event_type=event_type,
            actor=actor,
            details=details,
            now=now,
        )))

    @classmethod
    def _expire_if_due(
        cls, database: Session, row: FraudRiskEventRecord, now: datetime,
    ) -> bool:
        if row.status != "COOLING_OFF" or now < row.cooling_until:
            return False
        row.status = "EXPIRED"
        row.active_scope_key = None
        row.updated_at = now
        database.execute(update(FraudReleaseChallengeRecord).where(
            FraudReleaseChallengeRecord.event_id == row.event_id,
            FraudReleaseChallengeRecord.status == "PENDING",
        ).values(status="EXPIRED", updated_at=now))
        cls._audit(database, row, "COOLING_EXPIRED", "SYSTEM", {}, now)
        return True

    @staticmethod
    def _active_statement(owner_user_id: str, recipient_fingerprint: str):
        return select(FraudRiskEventRecord).where(
            FraudRiskEventRecord.owner_user_id == owner_user_id,
            FraudRiskEventRecord.recipient_fingerprint == recipient_fingerprint,
            FraudRiskEventRecord.status == "COOLING_OFF",
        ).with_for_update()

    def block_active_retry(
        self, owner_user_id: str, recipient_fingerprint: str,
        run_id: str | None, now: datetime,
    ) -> dict[str, Any] | None:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = database.scalar(self._active_statement(owner_user_id, recipient_fingerprint))
            if row is None or self._expire_if_due(database, row, now):
                return None
            row.retry_count += 1
            row.updated_at = now
            self._audit(
                database, row, "RETRY_BLOCKED", "AGENT",
                {"run_id": run_id, "retry_count": row.retry_count}, now,
            )
            database.flush()
            return self.event_to_dict(row)

    def trigger_or_reuse(self, draft: NewFraudRiskEvent) -> tuple[bool, dict[str, Any]]:
        try:
            with Session(self.engine, expire_on_commit=False) as database, database.begin():
                existing = database.scalar(self._active_statement(
                    draft.owner_user_id, draft.recipient_fingerprint
                ))
                if existing is not None and not self._expire_if_due(
                    database, existing, draft.triggered_at
                ):
                    existing.retry_count += 1
                    existing.updated_at = draft.triggered_at
                    self._audit(
                        database, existing, "RETRY_BLOCKED", "AGENT",
                        {"run_id": draft.trigger_run_id, "retry_count": existing.retry_count},
                        draft.triggered_at,
                    )
                    database.flush()
                    return False, self.event_to_dict(existing)
                row = FraudRiskEventRecord(
                    **draft.__dict__,
                    active_scope_key=_active_scope(
                        draft.owner_user_id, draft.recipient_fingerprint
                    ),
                    status="COOLING_OFF",
                    retry_count=0,
                    appeal_status="NONE",
                    created_at=draft.triggered_at,
                    updated_at=draft.triggered_at,
                )
                database.add(row)
                database.flush()
                self._audit(
                    database, row, "COOLING_STARTED", "POLICY_ENGINE",
                    {"signals": list(draft.signals), "trigger_run_id": draft.trigger_run_id},
                    draft.triggered_at,
                )
                database.flush()
                return True, self.event_to_dict(row)
        except IntegrityError:
            reused = self.block_active_retry(
                draft.owner_user_id, draft.recipient_fingerprint,
                draft.trigger_run_id, draft.triggered_at,
            )
            if reused is None:
                raise
            return False, reused

    def get(self, owner_user_id: str, event_id: str, now: datetime) -> dict[str, Any] | None:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = database.scalar(select(FraudRiskEventRecord).where(
                FraudRiskEventRecord.event_id == event_id,
                FraudRiskEventRecord.owner_user_id == owner_user_id,
            ).with_for_update())
            if row is None:
                return None
            self._expire_if_due(database, row, now)
            database.flush()
            return self.event_to_dict(row)

    def exists_for_other_owner(self, owner_user_id: str, event_id: str) -> bool:
        with Session(self.engine) as database:
            owner = database.scalar(select(FraudRiskEventRecord.owner_user_id).where(
                FraudRiskEventRecord.event_id == event_id
            ))
            return owner is not None and owner != owner_user_id

    def list(self, owner_user_id: str, status: str | None, now: datetime) -> list[dict[str, Any]]:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            due = database.scalars(select(FraudRiskEventRecord).where(
                FraudRiskEventRecord.owner_user_id == owner_user_id,
                FraudRiskEventRecord.status == "COOLING_OFF",
                FraudRiskEventRecord.cooling_until <= now,
            ).with_for_update()).all()
            for row in due:
                self._expire_if_due(database, row, now)
            statement = select(FraudRiskEventRecord).where(
                FraudRiskEventRecord.owner_user_id == owner_user_id
            )
            if status is not None:
                statement = statement.where(FraudRiskEventRecord.status == status)
            rows = database.scalars(statement.order_by(
                FraudRiskEventRecord.triggered_at.desc(), FraudRiskEventRecord.event_id
            )).all()
            return [self.event_to_dict(row) for row in rows]

    def audits(self, owner_user_id: str, event_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            rows = database.scalars(select(FraudRiskAuditRecord).where(
                FraudRiskAuditRecord.owner_user_id == owner_user_id,
                FraudRiskAuditRecord.event_id == event_id,
            ).order_by(FraudRiskAuditRecord.created_at, FraudRiskAuditRecord.audit_id)).all()
            return [self.audit_to_dict(row) for row in rows]

    def submit_appeal(
        self, owner_user_id: str, event_id: str, reason: str, now: datetime,
    ) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = database.scalar(select(FraudRiskEventRecord).where(
                FraudRiskEventRecord.event_id == event_id,
                FraudRiskEventRecord.owner_user_id == owner_user_id,
            ).with_for_update())
            if row is None:
                raise FraudRepositoryError("FRAUD_EVENT_NOT_FOUND")
            self._expire_if_due(database, row, now)
            if row.status != "COOLING_OFF":
                raise FraudRepositoryError("FRAUD_EVENT_NOT_ACTIVE")
            if row.appeal_status == "SUBMITTED":
                raise FraudRepositoryError("FRAUD_APPEAL_ALREADY_SUBMITTED")
            row.appeal_status = "SUBMITTED"
            row.appeal_reason = reason
            row.appeal_submitted_at = now
            row.updated_at = now
            self._audit(database, row, "APPEAL_SUBMITTED", "USER", {"reason": reason}, now)
            database.flush()
            return self.event_to_dict(row)

    def create_challenge(self, draft: NewFraudReleaseChallenge) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            event = database.scalar(select(FraudRiskEventRecord).where(
                FraudRiskEventRecord.event_id == draft.event_id,
                FraudRiskEventRecord.owner_user_id == draft.owner_user_id,
            ).with_for_update())
            if event is None:
                raise FraudRepositoryError("FRAUD_EVENT_NOT_FOUND")
            self._expire_if_due(database, event, draft.issued_at)
            if event.status != "COOLING_OFF":
                raise FraudRepositoryError("FRAUD_EVENT_NOT_ACTIVE")
            database.execute(update(FraudReleaseChallengeRecord).where(
                FraudReleaseChallengeRecord.event_id == draft.event_id,
                FraudReleaseChallengeRecord.status == "PENDING",
            ).values(status="SUPERSEDED", updated_at=draft.issued_at))
            row = FraudReleaseChallengeRecord(
                **draft.__dict__,
                status="PENDING",
                failed_attempts=0,
                delivery_channel="SMS",
                created_at=draft.issued_at,
                updated_at=draft.issued_at,
            )
            database.add(row)
            self._audit(
                database, event, "RELEASE_CHALLENGE_CREATED", "TRUSTED_APP",
                {"challenge_id": draft.challenge_id}, draft.issued_at,
            )
            database.flush()
            return self.challenge_to_dict(row)

    def get_challenge(
        self, owner_user_id: str, challenge_id: str, now: datetime,
    ) -> dict[str, Any] | None:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = database.scalar(select(FraudReleaseChallengeRecord).where(
                FraudReleaseChallengeRecord.challenge_id == challenge_id,
                FraudReleaseChallengeRecord.owner_user_id == owner_user_id,
            ).with_for_update())
            if row is None:
                return None
            if row.status == "PENDING" and now >= row.expires_at:
                row.status = "EXPIRED"
                row.updated_at = now
                event = database.get(FraudRiskEventRecord, row.event_id)
                self._audit(
                    database, event, "RELEASE_CHALLENGE_EXPIRED", "SYSTEM",
                    {"challenge_id": challenge_id}, now,
                )
            database.flush()
            return self.challenge_to_dict(row)

    def fail_challenge(
        self, owner_user_id: str, challenge_id: str, now: datetime, max_attempts: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            challenge = database.scalar(select(FraudReleaseChallengeRecord).where(
                FraudReleaseChallengeRecord.challenge_id == challenge_id,
                FraudReleaseChallengeRecord.owner_user_id == owner_user_id,
            ).with_for_update())
            if challenge is None:
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_FOUND")
            if challenge.status == "PENDING" and now >= challenge.expires_at:
                challenge.status = "EXPIRED"
            if challenge.status != "PENDING":
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_PENDING")
            challenge.failed_attempts += 1
            if challenge.failed_attempts >= max_attempts:
                challenge.status = "LOCKED"
            challenge.updated_at = now
            event = database.scalar(select(FraudRiskEventRecord).where(
                FraudRiskEventRecord.event_id == challenge.event_id
            ).with_for_update())
            self._audit(
                database, event, "RELEASE_VERIFICATION_FAILED", "TRUSTED_APP",
                {
                    "challenge_id": challenge_id,
                    "failed_attempts": challenge.failed_attempts,
                    "locked": challenge.status == "LOCKED",
                }, now,
            )
            database.flush()
            return self.event_to_dict(event), self.challenge_to_dict(challenge)

    def release(
        self, owner_user_id: str, challenge_id: str, now: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            challenge = database.scalar(select(FraudReleaseChallengeRecord).where(
                FraudReleaseChallengeRecord.challenge_id == challenge_id,
                FraudReleaseChallengeRecord.owner_user_id == owner_user_id,
            ).with_for_update())
            if challenge is None:
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_FOUND")
            if challenge.status == "PENDING" and now >= challenge.expires_at:
                challenge.status = "EXPIRED"
            if challenge.status != "PENDING":
                raise FraudRepositoryError("FRAUD_RELEASE_CHALLENGE_NOT_PENDING")
            event = database.scalar(select(FraudRiskEventRecord).where(
                FraudRiskEventRecord.event_id == challenge.event_id
            ).with_for_update())
            self._expire_if_due(database, event, now)
            if event.status != "COOLING_OFF":
                raise FraudRepositoryError("FRAUD_EVENT_NOT_ACTIVE")
            challenge.status = "VERIFIED"
            challenge.verified_at = now
            challenge.updated_at = now
            event.status = "RELEASED"
            event.active_scope_key = None
            event.released_at = now
            event.release_method = "TRUSTED_APP_MFA"
            event.updated_at = now
            self._audit(
                database, event, "COOLING_RELEASED", "TRUSTED_APP",
                {"challenge_id": challenge_id, "method": "MFA"}, now,
            )
            database.flush()
            return self.event_to_dict(event), self.challenge_to_dict(challenge)

    def reset_owner(self, owner_user_id: str) -> None:
        with Session(self.engine) as database, database.begin():
            database.execute(delete(FraudRiskEventRecord).where(
                FraudRiskEventRecord.owner_user_id == owner_user_id
            ))

    @staticmethod
    def event_to_dict(row: FraudRiskEventRecord) -> dict[str, Any]:
        return {
            "event_id": row.event_id,
            "owner_user_id": row.owner_user_id,
            "trigger_run_id": row.trigger_run_id,
            "recipient_fingerprint": row.recipient_fingerprint,
            "active_scope_key": row.active_scope_key,
            "recipient_display": row.recipient_display,
            "recipient_account_masked": row.recipient_account_masked,
            "status": row.status,
            "signals": list(row.signals),
            "evidence": deepcopy(row.evidence),
            "policy_version": row.policy_version,
            "message_digest": row.message_digest,
            "retry_count": row.retry_count,
            "appeal_status": row.appeal_status,
            "appeal_reason": row.appeal_reason,
            "appeal_submitted_at": row.appeal_submitted_at,
            "triggered_at": row.triggered_at,
            "cooling_until": row.cooling_until,
            "released_at": row.released_at,
            "release_method": row.release_method,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def challenge_to_dict(row: FraudReleaseChallengeRecord) -> dict[str, Any]:
        return {
            "challenge_id": row.challenge_id,
            "event_id": row.event_id,
            "owner_user_id": row.owner_user_id,
            "status": row.status,
            "otp_hash": row.otp_hash,
            "failed_attempts": row.failed_attempts,
            "delivery_channel": row.delivery_channel,
            "issued_at": row.issued_at,
            "expires_at": row.expires_at,
            "verified_at": row.verified_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def audit_to_dict(row: FraudRiskAuditRecord) -> dict[str, Any]:
        return {
            "audit_id": row.audit_id,
            "event_id": row.event_id,
            "owner_user_id": row.owner_user_id,
            "event_type": row.event_type,
            "actor": row.actor,
            "details": deepcopy(row.details),
            "created_at": row.created_at,
        }

