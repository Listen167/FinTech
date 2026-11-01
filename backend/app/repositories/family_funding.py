"""家庭资金协商仓库，负责用户隔离、持久化和审计序列化。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import (
    FamilyFundingNegotiationAuditRecord,
    FamilyFundingNegotiationRecord,
    FundingConsentScopeRecord,
)


class FundingIdempotencyConflictError(ValueError):
    pass


@dataclass(frozen=True)
class NewFundingNegotiation:
    negotiation_id: str
    owner_user_id: str
    source_run_id: str | None
    idempotency_key: str
    request_text: str
    status: str
    constraints: dict[str, Any]
    plans: list[dict[str, Any]]
    baseline_snapshot_hash: str | None
    workflow_checkpoint: dict[str, Any]
    model_trace: dict[str, Any]
    missing_fields: list[str]
    expires_at: datetime
    created_at: datetime
    request_digest: str


class FamilyFundingRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(self, draft: NewFundingNegotiation) -> dict[str, Any]:
        try:
            with Session(self.engine, expire_on_commit=False) as database, database.begin():
                existing = self._by_idempotency(database, draft.owner_user_id, draft.idempotency_key)
                if existing is not None:
                    return self._idempotent(existing, draft.request_digest)
                trace = deepcopy(draft.model_trace)
                trace["request_digest"] = draft.request_digest
                row = FamilyFundingNegotiationRecord(
                    negotiation_id=draft.negotiation_id,
                    owner_user_id=draft.owner_user_id,
                    source_run_id=draft.source_run_id,
                    idempotency_key=draft.idempotency_key,
                    request_text=draft.request_text,
                    status=draft.status,
                    constraints=deepcopy(draft.constraints),
                    plans=deepcopy(draft.plans),
                    selected_plan_id=None,
                    baseline_snapshot_hash=draft.baseline_snapshot_hash,
                    candidate_snapshot_hash=None,
                    workflow_checkpoint=deepcopy(draft.workflow_checkpoint),
                    model_trace=trace,
                    missing_fields=list(draft.missing_fields),
                    applied_changes=[],
                    expires_at=draft.expires_at,
                    created_at=draft.created_at,
                    updated_at=draft.created_at,
                )
                database.add(row)
                database.flush()
                self.add_audit(
                    database,
                    row=row,
                    event_type="NEGOTIATION_CREATED",
                    from_status=None,
                    to_status=draft.status,
                    actor="USER",
                    details={"model_status": trace.get("status")},
                    now=draft.created_at,
                )
                database.flush()
                return {"created": True, "negotiation": self.to_dict(row)}
        except IntegrityError:
            with Session(self.engine) as database:
                existing = self._by_idempotency(database, draft.owner_user_id, draft.idempotency_key)
                if existing is None:
                    raise
                return self._idempotent(existing, draft.request_digest)

    def get(self, owner_user_id: str, negotiation_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as database:
            row = database.scalar(select(FamilyFundingNegotiationRecord).where(
                FamilyFundingNegotiationRecord.negotiation_id == negotiation_id,
                FamilyFundingNegotiationRecord.owner_user_id == owner_user_id,
            ))
            return self.to_dict(row) if row is not None else None

    def exists_for_other_owner(self, owner_user_id: str, negotiation_id: str) -> bool:
        with Session(self.engine) as database:
            row = database.scalar(select(FamilyFundingNegotiationRecord.owner_user_id).where(
                FamilyFundingNegotiationRecord.negotiation_id == negotiation_id
            ))
            return row is not None and row != owner_user_id

    def list(self, owner_user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            statement = select(FamilyFundingNegotiationRecord).where(
                FamilyFundingNegotiationRecord.owner_user_id == owner_user_id
            )
            if status is not None:
                statement = statement.where(FamilyFundingNegotiationRecord.status == status)
            rows = database.scalars(statement.order_by(
                FamilyFundingNegotiationRecord.created_at.desc(),
                FamilyFundingNegotiationRecord.negotiation_id,
            )).all()
            return [self.to_dict(row) for row in rows]

    def audits(self, owner_user_id: str, negotiation_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            rows = database.scalars(select(FamilyFundingNegotiationAuditRecord).where(
                FamilyFundingNegotiationAuditRecord.owner_user_id == owner_user_id,
                FamilyFundingNegotiationAuditRecord.negotiation_id == negotiation_id,
            ).order_by(
                FamilyFundingNegotiationAuditRecord.created_at,
                FamilyFundingNegotiationAuditRecord.audit_id,
            )).all()
            return [self.audit_to_dict(row) for row in rows]

    def list_scopes(self, owner_user_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            rows = database.scalars(select(FundingConsentScopeRecord).where(
                FundingConsentScopeRecord.owner_user_id == owner_user_id
            ).order_by(FundingConsentScopeRecord.created_at.desc())).all()
            return [self.scope_to_dict(row) for row in rows]

    @staticmethod
    def add_audit(
        database: Session,
        *,
        row: FamilyFundingNegotiationRecord,
        event_type: str,
        from_status: str | None,
        to_status: str,
        actor: str,
        details: dict[str, Any],
        now: datetime,
    ) -> None:
        from uuid import uuid4

        database.add(FamilyFundingNegotiationAuditRecord(
            audit_id=f"fund_audit_{uuid4().hex[:16]}",
            negotiation_id=row.negotiation_id,
            owner_user_id=row.owner_user_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            details=deepcopy(details),
            created_at=now,
        ))

    @staticmethod
    def _by_idempotency(database: Session, owner: str, key: str):
        return database.scalar(select(FamilyFundingNegotiationRecord).where(
            FamilyFundingNegotiationRecord.owner_user_id == owner,
            FamilyFundingNegotiationRecord.idempotency_key == key,
        ))

    def _idempotent(self, row: FamilyFundingNegotiationRecord, digest: str) -> dict[str, Any]:
        if row.model_trace.get("request_digest") != digest:
            raise FundingIdempotencyConflictError("FUNDING_IDEMPOTENCY_CONFLICT")
        return {"created": False, "negotiation": self.to_dict(row)}

    @staticmethod
    def to_dict(row: FamilyFundingNegotiationRecord) -> dict[str, Any]:
        return {
            "negotiation_id": row.negotiation_id,
            "source_run_id": row.source_run_id,
            "status": row.status,
            "request_text": row.request_text,
            "constraints": deepcopy(row.constraints),
            "plans": deepcopy(row.plans),
            "selected_plan_id": row.selected_plan_id,
            "baseline_snapshot_hash": row.baseline_snapshot_hash,
            "candidate_snapshot_hash": row.candidate_snapshot_hash,
            "workflow": deepcopy(row.workflow_checkpoint),
            "model_trace": {key: value for key, value in row.model_trace.items() if key != "request_digest"},
            "missing_fields": list(row.missing_fields),
            "applied_changes": deepcopy(row.applied_changes),
            "expires_at": row.expires_at.isoformat(),
            "selected_at": row.selected_at.isoformat() if row.selected_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "funds_execution_allowed": False,
        }

    @staticmethod
    def audit_to_dict(row: FamilyFundingNegotiationAuditRecord) -> dict[str, Any]:
        return {
            "audit_id": row.audit_id,
            "negotiation_id": row.negotiation_id,
            "event_type": row.event_type,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "actor": row.actor,
            "details": deepcopy(row.details),
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def scope_to_dict(row: FundingConsentScopeRecord) -> dict[str, Any]:
        return {
            "scope_id": row.scope_id,
            "status": row.status,
            "goal_node_ids": list(row.goal_node_ids),
            "amount_lower_bound": format(row.amount_lower_bound, ".2f"),
            "amount_upper_bound": format(row.amount_upper_bound, ".2f"),
            "protected_balance": format(row.protected_balance, ".2f"),
            "max_single_adjustment": format(row.max_single_adjustment, ".2f"),
            "auto_apply_enabled": False,
            "valid_from": row.valid_from.isoformat(),
            "valid_until": row.valid_until.isoformat(),
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
