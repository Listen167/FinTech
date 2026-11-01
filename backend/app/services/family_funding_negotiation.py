"""家庭资金目标协商服务：候选、二次确认、并发失效、撤销与授权范围。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AccountRecord,
    AgentRunRecord,
    FamilyFundingNegotiationRecord,
    FinancialGoalRecord,
    FundingConsentScopeRecord,
    LifeGraphNodeRecord,
    TransactionRecord,
    utc_now,
)
from backend.app.errors import ApplicationError
from backend.app.repositories.family_funding import (
    FamilyFundingRepository,
    FundingIdempotencyConflictError,
    NewFundingNegotiation,
)
from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.schemas.family_funding import (
    ConstraintEvidence,
    CreateFundingConsentScopeRequest,
    EvidenceSource,
    FundingPlanningRequest,
    GoalAdjustment,
    PlanFeasibility,
)
from backend.app.services.family_funding_planner import FamilyFundingPlanner
from backend.app.services.family_funding_workflow import FamilyFundingWorkflow
from backend.app.services.funding_constraint_extractor import FundingConstraintExtractor


class FamilyFundingError(ApplicationError):
    pass


class FamilyFundingNegotiationService:
    def __init__(
        self,
        engine: Engine,
        repository: FamilyFundingRepository | None = None,
        planner: FamilyFundingPlanner | None = None,
        extractor: FundingConstraintExtractor | None = None,
        workflow: FamilyFundingWorkflow | None = None,
    ) -> None:
        self.engine = engine
        self.repository = repository or FamilyFundingRepository(engine)
        self.planner = planner or FamilyFundingPlanner()
        self.extractor = extractor or FundingConstraintExtractor()
        self.workflow = workflow or FamilyFundingWorkflow(self.planner)

    def create(
        self,
        *,
        owner_user_id: str,
        idempotency_key: str,
        request_text: str,
        planning: FundingPlanningRequest | None,
        source_run_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        created_at = now or utc_now()
        if source_run_id:
            with Session(self.engine) as database:
                run = database.get(AgentRunRecord, source_run_id)
                if run is None:
                    raise FamilyFundingError("SOURCE_RUN_NOT_FOUND")
                if run.user_id != owner_user_id:
                    raise FamilyFundingError("RESOURCE_FORBIDDEN")

        model_trace: dict[str, Any]
        missing_fields: list[str]
        if planning is None:
            trusted_context = self._trusted_context(owner_user_id)
            planning, missing_fields, model_trace = self.extractor.extract(
                request_text=request_text,
                trusted_context=trusted_context,
            )
        else:
            missing_fields = []
            model_trace = {
                "provider": "structured_input",
                "status": "NOT_CALLED",
                "fallback_used": False,
            }

        normalized: FundingPlanningRequest | None = None
        snapshot_hash: str | None = None
        if planning is not None:
            with Session(self.engine) as database:
                normalized, snapshot_hash = self._normalize_and_snapshot(
                    database, owner_user_id, planning, lock=False
                )

        workflow_state = self.workflow.invoke({
            "event": "CREATE",
            "planning": normalized.model_dump(mode="json") if normalized else {},
            "missing_fields": missing_fields,
            "trace": [],
        })
        status = workflow_state["stage"]
        plans = list(workflow_state.get("plans", []))
        constraints = normalized.model_dump(mode="json") if normalized else {}
        digest = self._request_digest(request_text, planning, source_run_id)
        try:
            result = self.repository.create(NewFundingNegotiation(
                negotiation_id=f"funding_{uuid4().hex[:16]}",
                owner_user_id=owner_user_id,
                source_run_id=source_run_id,
                idempotency_key=idempotency_key,
                request_text=request_text,
                status=status,
                constraints=constraints,
                plans=plans,
                baseline_snapshot_hash=snapshot_hash,
                workflow_checkpoint=self.workflow.checkpoint(workflow_state),
                model_trace=model_trace,
                missing_fields=missing_fields,
                expires_at=created_at + timedelta(minutes=30),
                created_at=created_at,
                request_digest=digest,
            ))
        except FundingIdempotencyConflictError as error:
            raise FamilyFundingError(str(error)) from error
        return result

    def get(self, owner_user_id: str, negotiation_id: str) -> dict[str, Any]:
        result = self.repository.get(owner_user_id, negotiation_id)
        if result is None:
            if self.repository.exists_for_other_owner(owner_user_id, negotiation_id):
                raise FamilyFundingError("RESOURCE_FORBIDDEN")
            raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_FOUND")
        return result

    def list(self, owner_user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list(owner_user_id, status)

    def audits(self, owner_user_id: str, negotiation_id: str) -> list[dict[str, Any]]:
        self.get(owner_user_id, negotiation_id)
        return self.repository.audits(owner_user_id, negotiation_id)

    def context(self, owner_user_id: str) -> dict[str, Any]:
        graph = LifeGraphRepository(self.engine).get_graph(owner_user_id)
        labels = {
            node["node_id"]: node["display_label"]
            for node in graph["nodes"] if node["node_type"] == "GOAL"
        }
        with Session(self.engine) as database:
            accounts = database.scalars(select(AccountRecord).where(
                AccountRecord.user_id == owner_user_id
            ).order_by(AccountRecord.id)).all()
        return {
            "accounts": [
                {
                    "account_id": row.id,
                    "currency": row.payload.get("currency", "CNY"),
                    "available_balance": format(
                        Decimal(str(row.payload.get("available_balance", 0))), ".2f"
                    ),
                }
                for row in accounts
                if row.payload.get("currency", "CNY") == "CNY"
            ],
            "goals": [
                {
                    **goal,
                    "label": labels.get(goal["goal_node_id"], "未命名目标"),
                }
                for goal in graph["goals"]
                if goal["status"] not in {"COMPLETED", "CANCELED"}
                and goal["due_at"] is not None
                and goal["currency"] == "CNY"
            ],
            "formal_goal_write_requires_confirmation": True,
            "funds_execution_allowed": False,
        }

    def complete_constraints(
        self,
        *,
        owner_user_id: str,
        negotiation_id: str,
        planning: FundingPlanningRequest,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        completed_at = now or utc_now()
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = self._locked_negotiation(database, owner_user_id, negotiation_id)
            if row.status != "WAITING_INPUT":
                raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_COMPLETABLE")
            normalized, snapshot = self._normalize_and_snapshot(
                database, owner_user_id, planning, lock=True
            )
            state = self.workflow.invoke({
                "event": "CREATE",
                "planning": normalized.model_dump(mode="json"),
                "missing_fields": [],
                "trace": [],
            })
            previous = row.status
            row.status = "WAITING_SELECTION"
            row.constraints = normalized.model_dump(mode="json")
            row.plans = list(state.get("plans", []))
            row.missing_fields = []
            row.baseline_snapshot_hash = snapshot
            row.workflow_checkpoint = self.workflow.checkpoint(state, row.workflow_checkpoint)
            row.updated_at = completed_at
            self.repository.add_audit(
                database, row=row, event_type="CONSTRAINTS_COMPLETED",
                from_status=previous, to_status=row.status, actor="USER",
                details={"reason": reason[:500]}, now=completed_at,
            )
        return self.get(owner_user_id, negotiation_id)

    def add_custom_plan(
        self,
        *,
        owner_user_id: str,
        negotiation_id: str,
        title: str,
        model_explanation: str,
        adjustments: list[GoalAdjustment],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        proposed_at = now or utc_now()
        terminal_error: FamilyFundingError | None = None
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = self._locked_negotiation(database, owner_user_id, negotiation_id)
            if row.status != "WAITING_SELECTION":
                raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_SELECTABLE")
            planning = FundingPlanningRequest.model_validate(row.constraints)
            try:
                plan = self.planner.validate_custom_plan(
                    planning, adjustments, title=title
                )
            except ValueError as error:
                self.repository.add_audit(
                    database, row=row, event_type="MODEL_PLAN_REJECTED",
                    from_status=row.status, to_status=row.status, actor="MODEL",
                    details={"error_code": str(error)}, now=proposed_at,
                )
                terminal_error = FamilyFundingError(
                    "FUNDING_MODEL_PLAN_REJECTED",
                    details={"validation_code": str(error)},
                )
            else:
                plan.model_explanation = model_explanation
                existing = [item for item in row.plans if item["plan_id"] != plan.plan_id]
                row.plans = [*existing, plan.model_dump(mode="json")]
                row.updated_at = proposed_at
                self.repository.add_audit(
                    database, row=row, event_type="MODEL_PLAN_VALIDATED",
                    from_status=row.status, to_status=row.status, actor="MODEL",
                    details={"plan_id": plan.plan_id, "feasibility": plan.feasibility.value},
                    now=proposed_at,
                )
        if terminal_error:
            raise terminal_error
        return self.get(owner_user_id, negotiation_id)

    def select_plan(
        self,
        *,
        owner_user_id: str,
        negotiation_id: str,
        plan_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        selected_at = now or utc_now()
        terminal_error: FamilyFundingError | None = None
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = self._locked_negotiation(database, owner_user_id, negotiation_id)
            if row.status != "WAITING_SELECTION":
                raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_SELECTABLE")
            if selected_at > row.expires_at:
                previous = row.status
                row.status = "CANCELED"
                row.updated_at = selected_at
                self.repository.add_audit(
                    database, row=row, event_type="NEGOTIATION_EXPIRED",
                    from_status=previous, to_status=row.status, actor="SYSTEM",
                    details={}, now=selected_at,
                )
                terminal_error = FamilyFundingError("FUNDING_NEGOTIATION_EXPIRED")
            else:
                planning = FundingPlanningRequest.model_validate(row.constraints)
                normalized, snapshot = self._normalize_and_snapshot(
                    database, owner_user_id, planning, lock=True
                )
                if snapshot != row.baseline_snapshot_hash:
                    self._recalculate_stale(
                        database, row, normalized, snapshot, selected_at, "SELECTION_SNAPSHOT_CHANGED"
                    )
                    terminal_error = FamilyFundingError("FUNDING_SNAPSHOT_CHANGED")
                else:
                    plan = next((item for item in row.plans if item["plan_id"] == plan_id), None)
                    if plan is None:
                        raise FamilyFundingError("FUNDING_PLAN_NOT_FOUND")
                    if plan["feasibility"] == PlanFeasibility.INFEASIBLE.value:
                        raise FamilyFundingError("FUNDING_PLAN_INFEASIBLE")
                    previous = row.status
                    workflow_state = self.workflow.invoke({
                        "event": "SELECT", "selected_plan_id": plan_id, "trace": []
                    })
                    row.status = "PENDING_CONFIRMATION"
                    row.selected_plan_id = plan_id
                    row.candidate_snapshot_hash = snapshot
                    row.selected_at = selected_at
                    row.workflow_checkpoint = self.workflow.checkpoint(
                        workflow_state, row.workflow_checkpoint
                    )
                    row.updated_at = selected_at
                    self.repository.add_audit(
                        database, row=row, event_type="PLAN_SELECTED",
                        from_status=previous, to_status=row.status, actor="USER",
                        details={"plan_id": plan_id, "reason": reason[:500]}, now=selected_at,
                    )
        if terminal_error:
            raise terminal_error
        return self.get(owner_user_id, negotiation_id)

    def confirm(
        self,
        *,
        owner_user_id: str,
        negotiation_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        confirmed_at = now or utc_now()
        terminal_error: FamilyFundingError | None = None
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = self._locked_negotiation(database, owner_user_id, negotiation_id)
            if row.status != "PENDING_CONFIRMATION" or not row.selected_plan_id:
                raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_CONFIRMABLE")
            planning = FundingPlanningRequest.model_validate(row.constraints)
            normalized, snapshot = self._normalize_and_snapshot(
                database, owner_user_id, planning, lock=True
            )
            if confirmed_at > row.expires_at or snapshot != row.candidate_snapshot_hash:
                self._recalculate_stale(
                    database,
                    row,
                    normalized,
                    snapshot,
                    confirmed_at,
                    "CONFIRMATION_EXPIRED" if confirmed_at > row.expires_at else "CONFIRMATION_SNAPSHOT_CHANGED",
                )
                terminal_error = FamilyFundingError("FUNDING_SNAPSHOT_CHANGED")
            else:
                plan = next(
                    (item for item in row.plans if item["plan_id"] == row.selected_plan_id), None
                )
                if plan is None or plan["feasibility"] == PlanFeasibility.INFEASIBLE.value:
                    raise FamilyFundingError("FUNDING_PLAN_NOT_FOUND")
                changes = self._apply_adjustments(
                    database,
                    owner_user_id,
                    [GoalAdjustment.model_validate(item) for item in plan["adjustments"]],
                    confirmed_at,
                )
                previous = row.status
                workflow_state = self.workflow.invoke({"event": "CONFIRM", "trace": []})
                row.status = "APPLIED"
                row.applied_changes = changes
                row.decided_at = confirmed_at
                row.workflow_checkpoint = self.workflow.checkpoint(
                    workflow_state, row.workflow_checkpoint
                )
                row.updated_at = confirmed_at
                self.repository.add_audit(
                    database, row=row, event_type="CANDIDATE_CONFIRMED",
                    from_status=previous, to_status=row.status, actor="USER",
                    details={
                        "plan_id": row.selected_plan_id,
                        "reason": reason[:500],
                        "changes": changes,
                        "funds_execution_allowed": False,
                    },
                    now=confirmed_at,
                )
        if terminal_error:
            raise terminal_error
        return self.get(owner_user_id, negotiation_id)

    def transition(
        self,
        *,
        owner_user_id: str,
        negotiation_id: str,
        command: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        transition_time = now or utc_now()
        mapping = {
            "reject": ("REJECTED", "REJECT", "PLAN_REJECTED"),
            "cancel": ("CANCELED", "CANCEL", "NEGOTIATION_CANCELED"),
            "handoff": ("WAITING_HUMAN", "HANDOFF", "HUMAN_HANDOFF_REQUESTED"),
        }
        if command not in mapping:
            raise FamilyFundingError("INVALID_REQUEST")
        target, event, audit_event = mapping[command]
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = self._locked_negotiation(database, owner_user_id, negotiation_id)
            if row.status not in {"WAITING_INPUT", "WAITING_SELECTION", "PENDING_CONFIRMATION"}:
                raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_CONTROLLABLE")
            previous = row.status
            state = self.workflow.invoke({"event": event, "trace": []})
            row.status = target
            row.workflow_checkpoint = self.workflow.checkpoint(state, row.workflow_checkpoint)
            row.decided_at = transition_time if target in {"REJECTED", "CANCELED"} else None
            row.updated_at = transition_time
            self.repository.add_audit(
                database, row=row, event_type=audit_event,
                from_status=previous, to_status=target, actor="USER",
                details={"reason": reason[:500]}, now=transition_time,
            )
        return self.get(owner_user_id, negotiation_id)

    def undo(
        self,
        *,
        owner_user_id: str,
        negotiation_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        undo_time = now or utc_now()
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = self._locked_negotiation(database, owner_user_id, negotiation_id)
            if row.status != "APPLIED" or not row.applied_changes:
                raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_UNDOABLE")
            goal_ids = [item["goal_id"] for item in row.applied_changes]
            goals = database.scalars(select(FinancialGoalRecord).where(
                FinancialGoalRecord.owner_user_id == owner_user_id,
                FinancialGoalRecord.goal_node_id.in_(goal_ids),
            ).with_for_update()).all()
            by_id = {item.goal_node_id: item for item in goals}
            for change in row.applied_changes:
                goal = by_id.get(change["goal_id"])
                if goal is None or self._goal_state(goal) != change["after"]:
                    raise FamilyFundingError("FUNDING_UNDO_CONFLICT")
            for change in row.applied_changes:
                goal = by_id[change["goal_id"]]
                before = change["before"]
                goal.target_amount = Decimal(before["target_amount"])
                goal.due_at = datetime.fromisoformat(before["due_at"]) if before["due_at"] else None
                goal.funding_policy = deepcopy(before["funding_policy"])
                goal.updated_at = undo_time
            state = self.workflow.invoke({"event": "UNDO", "trace": []})
            previous = row.status
            row.status = "UNDONE"
            row.workflow_checkpoint = self.workflow.checkpoint(state, row.workflow_checkpoint)
            row.decided_at = undo_time
            row.updated_at = undo_time
            self.repository.add_audit(
                database, row=row, event_type="APPLIED_CHANGES_UNDONE",
                from_status=previous, to_status=row.status, actor="USER",
                details={"reason": reason[:500], "restored_goal_ids": goal_ids}, now=undo_time,
            )
        return self.get(owner_user_id, negotiation_id)

    def create_scope(
        self,
        *,
        owner_user_id: str,
        payload: CreateFundingConsentScopeRequest,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        created_at = now or utc_now()
        valid_until = payload.valid_until.replace(tzinfo=None)
        if valid_until <= created_at:
            raise FamilyFundingError("FUNDING_CONSENT_SCOPE_EXPIRED")
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            count = len(database.scalars(select(FinancialGoalRecord).where(
                FinancialGoalRecord.owner_user_id == owner_user_id,
                FinancialGoalRecord.goal_node_id.in_(payload.goal_node_ids),
            )).all())
            if count != len(set(payload.goal_node_ids)):
                raise FamilyFundingError("GOAL_RECORD_NOT_FOUND")
            row = FundingConsentScopeRecord(
                scope_id=f"fund_scope_{uuid4().hex[:16]}",
                owner_user_id=owner_user_id,
                status="ACTIVE",
                goal_node_ids=sorted(set(payload.goal_node_ids)),
                amount_lower_bound=payload.amount_lower_bound,
                amount_upper_bound=payload.amount_upper_bound,
                protected_balance=payload.protected_balance,
                max_single_adjustment=payload.max_single_adjustment,
                auto_apply_enabled=False,
                valid_from=payload.valid_from.replace(tzinfo=None),
                valid_until=valid_until,
                revoked_at=None,
                created_at=created_at,
                updated_at=created_at,
            )
            database.add(row)
            database.flush()
            return self.repository.scope_to_dict(row)

    def list_scopes(self, owner_user_id: str) -> list[dict[str, Any]]:
        current = utc_now()
        with Session(self.engine) as database, database.begin():
            rows = database.scalars(select(FundingConsentScopeRecord).where(
                FundingConsentScopeRecord.owner_user_id == owner_user_id,
                FundingConsentScopeRecord.status == "ACTIVE",
                FundingConsentScopeRecord.valid_until <= current,
            ).with_for_update()).all()
            for row in rows:
                row.status = "EXPIRED"
                row.updated_at = current
        return self.repository.list_scopes(owner_user_id)

    def revoke_scope(
        self, *, owner_user_id: str, scope_id: str, now: datetime | None = None
    ) -> dict[str, Any]:
        revoked_at = now or utc_now()
        terminal_error: FamilyFundingError | None = None
        result: dict[str, Any] | None = None
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            row = database.scalar(select(FundingConsentScopeRecord).where(
                FundingConsentScopeRecord.scope_id == scope_id
            ).with_for_update())
            if row is None:
                raise FamilyFundingError("FUNDING_CONSENT_SCOPE_NOT_FOUND")
            if row.owner_user_id != owner_user_id:
                raise FamilyFundingError("RESOURCE_FORBIDDEN")
            if row.status != "ACTIVE":
                raise FamilyFundingError("FUNDING_CONSENT_SCOPE_NOT_ACTIVE")
            if row.valid_until <= revoked_at:
                row.status = "EXPIRED"
                row.updated_at = revoked_at
                terminal_error = FamilyFundingError("FUNDING_CONSENT_SCOPE_EXPIRED")
            else:
                row.status = "REVOKED"
                row.revoked_at = revoked_at
                row.updated_at = revoked_at
                result = self.repository.scope_to_dict(row)
        if terminal_error:
            raise terminal_error
        if result is None:
            raise FamilyFundingError("FUNDING_CONSENT_SCOPE_NOT_ACTIVE")
        return result

    def _trusted_context(self, owner_user_id: str) -> dict[str, Any]:
        graph = LifeGraphRepository(self.engine).get_graph(owner_user_id)
        with Session(self.engine) as database:
            accounts = database.scalars(select(AccountRecord).where(
                AccountRecord.user_id == owner_user_id
            )).all()
        return {
            "accounts": [
                {
                    "account_id": row.id,
                    "currency": row.payload.get("currency", "CNY"),
                    "available_balance": row.payload.get("available_balance"),
                    "source": "BANK_CONFIRMED",
                }
                for row in accounts
            ],
            "goals": graph["goals"],
            "goal_labels": {
                node["node_id"]: node["display_label"]
                for node in graph["nodes"] if node["node_type"] == "GOAL"
            },
        }

    def _normalize_and_snapshot(
        self,
        database: Session,
        owner_user_id: str,
        planning: FundingPlanningRequest,
        *,
        lock: bool,
    ) -> tuple[FundingPlanningRequest, str]:
        account_stmt = select(AccountRecord).where(
            AccountRecord.user_id == owner_user_id,
            AccountRecord.id.in_(planning.constraints.allowed_account_ids),
        )
        goal_ids = [item.goal_id for item in planning.goals]
        goal_stmt = select(FinancialGoalRecord).where(
            FinancialGoalRecord.owner_user_id == owner_user_id,
            FinancialGoalRecord.goal_node_id.in_(goal_ids),
        )
        if lock:
            account_stmt = account_stmt.with_for_update()
            goal_stmt = goal_stmt.with_for_update()
        accounts = database.scalars(account_stmt).all()
        goals = database.scalars(goal_stmt).all()
        if len(accounts) != len(set(planning.constraints.allowed_account_ids)):
            raise FamilyFundingError("FUNDING_ACCOUNT_NOT_FOUND")
        if len(goals) != len(set(goal_ids)):
            raise FamilyFundingError("GOAL_RECORD_NOT_FOUND")
        if any(row.payload.get("currency", "CNY") != "CNY" for row in accounts):
            raise FamilyFundingError("FUNDING_CURRENCY_NOT_SUPPORTED")
        for income in planning.constraints.incomes:
            if income.source != EvidenceSource.BANK_CONFIRMED:
                continue
            transaction = (
                database.get(TransactionRecord, income.reference_id)
                if income.reference_id else None
            )
            payload = (
                transaction.payload
                if transaction is not None and transaction.user_id == owner_user_id
                else {}
            )
            try:
                occurred_on = datetime.fromisoformat(payload.get("occurred_at", "")).date()
                amount = Decimal(str(payload.get("amount")))
            except (ValueError, TypeError):
                occurred_on = None
                amount = Decimal("0.00")
            if occurred_on != income.available_on or amount != income.amount or amount <= 0:
                raise FamilyFundingError("FUNDING_BANK_INCOME_UNVERIFIED")
        by_goal = {row.goal_node_id: row for row in goals}
        normalized_goals = []
        for item in planning.goals:
            row = by_goal[item.goal_id]
            if row.due_at is None:
                raise FamilyFundingError("FUNDING_GOAL_DUE_DATE_REQUIRED")
            data = item.model_dump()
            data.update({
                "target_amount": row.target_amount,
                "reserved_amount": row.reserved_amount,
                "due_date": row.due_at.date(),
            })
            if item.rigidity.value == "RIGID":
                data["minimum_amount"] = row.target_amount
            elif item.minimum_amount > row.target_amount:
                data["minimum_amount"] = row.target_amount
            normalized_goals.append(type(item).model_validate(data))
        balance = sum(
            (Decimal(str(row.payload.get("available_balance", 0))) for row in accounts),
            start=Decimal("0.00"),
        )
        evidence = [
            item for item in planning.constraints.evidence if item.field_name != "starting_balance"
        ]
        evidence.append(ConstraintEvidence(
            field_name="starting_balance",
            source=EvidenceSource.BANK_CONFIRMED,
            confidence_bp=10000,
            reference_id="+".join(sorted(row.id for row in accounts)),
        ))
        constraints = planning.constraints.model_copy(update={
            "starting_balance": balance,
            "evidence": evidence,
        })
        normalized = planning.model_copy(update={"goals": normalized_goals, "constraints": constraints})
        snapshot = self._snapshot_hash(accounts, goals, normalized)
        return normalized, snapshot

    def _recalculate_stale(
        self,
        database: Session,
        row: FamilyFundingNegotiationRecord,
        normalized: FundingPlanningRequest,
        snapshot: str,
        now: datetime,
        reason: str,
    ) -> None:
        result = self.planner.build_plans(normalized, generated_at=now)
        previous = row.status
        row.status = "WAITING_SELECTION"
        row.constraints = normalized.model_dump(mode="json")
        row.plans = [item.model_dump(mode="json") for item in result.plans]
        row.selected_plan_id = None
        row.selected_at = None
        row.baseline_snapshot_hash = snapshot
        row.candidate_snapshot_hash = None
        row.applied_changes = []
        state = {
            "stage": "WAITING_SELECTION",
            "trace": ["load_context", "build_timeline", "calculate_plans", "await_selection"],
            "error_code": "FUNDING_SNAPSHOT_CHANGED",
        }
        row.workflow_checkpoint = self.workflow.checkpoint(state, row.workflow_checkpoint)
        row.updated_at = now
        self.repository.add_audit(
            database, row=row, event_type="PLAN_INVALIDATED_AND_RECALCULATED",
            from_status=previous, to_status=row.status, actor="SYSTEM",
            details={"reason": reason, "new_snapshot_hash": snapshot}, now=now,
        )

    def _apply_adjustments(
        self,
        database: Session,
        owner_user_id: str,
        adjustments: list[GoalAdjustment],
        now: datetime,
    ) -> list[dict[str, Any]]:
        if not adjustments:
            return []
        ids = [item.goal_id for item in adjustments]
        rows = database.scalars(select(FinancialGoalRecord).where(
            FinancialGoalRecord.owner_user_id == owner_user_id,
            FinancialGoalRecord.goal_node_id.in_(ids),
        ).with_for_update()).all()
        by_id = {row.goal_node_id: row for row in rows}
        if len(by_id) != len(set(ids)):
            raise FamilyFundingError("GOAL_RECORD_NOT_FOUND")
        changes = []
        for item in adjustments:
            goal = by_id[item.goal_id]
            before = self._goal_state(goal)
            if item.target_amount is not None:
                if item.target_amount < goal.reserved_amount:
                    raise FamilyFundingError("TARGET_AMOUNT_BELOW_RESERVED")
                goal.target_amount = item.target_amount
            if item.due_date is not None:
                goal.due_at = datetime.combine(item.due_date, goal.due_at.time() if goal.due_at else time.min)
            if item.funding_policy_patch:
                goal.funding_policy = {**deepcopy(goal.funding_policy), **deepcopy(item.funding_policy_patch)}
            goal.updated_at = now
            changes.append({"goal_id": item.goal_id, "before": before, "after": self._goal_state(goal)})
        return changes

    def _locked_negotiation(
        self, database: Session, owner_user_id: str, negotiation_id: str
    ) -> FamilyFundingNegotiationRecord:
        row = database.scalar(select(FamilyFundingNegotiationRecord).where(
            FamilyFundingNegotiationRecord.negotiation_id == negotiation_id
        ).with_for_update())
        if row is None:
            raise FamilyFundingError("FUNDING_NEGOTIATION_NOT_FOUND")
        if row.owner_user_id != owner_user_id:
            raise FamilyFundingError("RESOURCE_FORBIDDEN")
        return row

    @staticmethod
    def _goal_state(goal: FinancialGoalRecord) -> dict[str, Any]:
        return {
            "target_amount": format(goal.target_amount, ".2f"),
            "due_at": goal.due_at.isoformat() if goal.due_at else None,
            "funding_policy": deepcopy(goal.funding_policy),
        }

    @staticmethod
    def _snapshot_hash(accounts, goals, planning: FundingPlanningRequest) -> str:
        payload = {
            "accounts": sorted([
                {
                    "id": row.id,
                    "balance": format(Decimal(str(row.payload.get("available_balance", 0))), ".2f"),
                    "currency": row.payload.get("currency", "CNY"),
                }
                for row in accounts
            ], key=lambda item: item["id"]),
            "goals": sorted([
                {
                    "id": row.goal_node_id,
                    "target": format(row.target_amount, ".2f"),
                    "reserved": format(row.reserved_amount, ".2f"),
                    "due_at": row.due_at.isoformat() if row.due_at else None,
                    "funding_policy": row.funding_policy,
                }
                for row in goals
            ], key=lambda item: item["id"]),
            "planning_constraints": planning.model_dump(mode="json"),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _request_digest(
        request_text: str,
        planning: FundingPlanningRequest | None,
        source_run_id: str | None,
    ) -> str:
        payload = {
            "request_text": request_text,
            "planning": planning.model_dump(mode="json") if planning else None,
            "source_run_id": source_run_id,
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
