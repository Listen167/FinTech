"""生活资金目标冲突检测，只提供可解释建议，不自动调整目标。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.app.db.models import AccountRecord, TransactionRecord, utc_now
from backend.app.errors import ApplicationError
from backend.app.repositories.life_graph import LifeGraphRepository, hash_change_set
from backend.app.schemas.life_graph import LifeGraphChangeSet


class LifeGoalConflictError(ApplicationError):
    pass


class LifeGoalConflictService:
    def __init__(self, engine: Engine, repository: LifeGraphRepository | None = None) -> None:
        self.engine = engine
        self.repository = repository or LifeGraphRepository(engine)

    def analyze(
        self,
        *,
        owner_user_id: str,
        candidate_request_id: str | None = None,
        protected_balance: Decimal = Decimal("0.00"),
        horizon_months: int = 12,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        if protected_balance < 0:
            raise LifeGoalConflictError("PROTECTED_BALANCE_OUT_OF_RANGE")
        if not 1 <= horizon_months <= 24:
            raise LifeGoalConflictError("HORIZON_MONTHS_OUT_OF_RANGE")

        analysis_time = as_of or utc_now()
        graph = self.repository.get_graph(owner_user_id)
        candidate_change_set = self._candidate_change_set(
            owner_user_id, candidate_request_id
        )
        goals = self._collect_goals(graph, candidate_change_set)
        currencies = {goal["currency"] for goal in goals}
        if currencies - {"CNY"}:
            return self._unknown_result(
                analysis_time,
                goals,
                protected_balance,
                "UNSUPPORTED_GOAL_CURRENCY",
            )

        available_balance, cash_flow = self._financial_evidence(
            owner_user_id, analysis_time.date()
        )
        if available_balance is None:
            return self._unknown_result(
                analysis_time,
                goals,
                protected_balance,
                "ACCOUNT_BALANCE_UNAVAILABLE",
                cash_flow=cash_flow,
            )

        spendable_balance = max(available_balance - protected_balance, Decimal("0.00"))
        horizon_end = self._add_months(analysis_time.date(), horizon_months)
        scheduled_goals = [
            goal
            for goal in goals
            if goal["due_date"] is not None
            and goal["due_date"] <= horizon_end
        ]
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for goal in scheduled_goals:
            due_date = max(goal["due_date"], analysis_time.date())
            grouped[due_date.strftime("%Y-%m")].append(goal)

        monthly_capacity = cash_flow["net"] if cash_flow["available"] else None
        if monthly_capacity is not None:
            monthly_capacity = max(monthly_capacity, Decimal("0.00"))

        windows: list[dict[str, Any]] = []
        cumulative_remaining = Decimal("0.00")
        has_liquidity_conflict = False
        has_cash_flow_pressure = False
        for period in sorted(grouped):
            period_goals = grouped[period]
            period_total = sum(
                (goal["remaining_amount"] for goal in period_goals),
                start=Decimal("0.00"),
            )
            cumulative_remaining += period_total
            liquidity_gap = max(cumulative_remaining - spendable_balance, Decimal("0.00"))
            cash_flow_gap = (
                max(period_total - monthly_capacity, Decimal("0.00"))
                if monthly_capacity is not None
                else None
            )
            has_liquidity_conflict = has_liquidity_conflict or liquidity_gap > 0
            has_cash_flow_pressure = has_cash_flow_pressure or (
                cash_flow_gap is not None and cash_flow_gap > 0
            )
            windows.append({
                "period": period,
                "goals": [self._public_goal(goal) for goal in period_goals],
                "period_goal_total": self._money(period_total),
                "cumulative_goal_total": self._money(cumulative_remaining),
                "liquidity_gap": self._money(liquidity_gap),
                "cash_flow_gap": self._money(cash_flow_gap),
                "status": (
                    "CONFLICT"
                    if liquidity_gap > 0
                    else "PRESSURE"
                    if cash_flow_gap is not None and cash_flow_gap > 0
                    else "CLEAR"
                ),
            })

        status = (
            "CONFLICT"
            if has_liquidity_conflict
            else "PRESSURE"
            if has_cash_flow_pressure
            else "CLEAR"
        )
        severity = {"CONFLICT": "HIGH", "PRESSURE": "MEDIUM", "CLEAR": "NONE"}[status]
        total_remaining = sum(
            (goal["remaining_amount"] for goal in scheduled_goals),
            start=Decimal("0.00"),
        )
        unscheduled = [goal for goal in goals if goal["due_date"] is None]
        return {
            "status": status,
            "severity": severity,
            "as_of": analysis_time.isoformat(),
            "currency": "CNY",
            "priority_decision": "USER_REQUIRED",
            "evidence": {
                "available_balance": self._money(available_balance),
                "protected_balance": self._money(protected_balance),
                "spendable_balance": self._money(spendable_balance),
                "observed_30d_income": self._money(cash_flow["income"]),
                "observed_30d_expense": self._money(cash_flow["expense"]),
                "observed_30d_net_cash_flow": self._money(cash_flow["net"]),
                "cash_flow_evidence_available": cash_flow["available"],
                "formal_goal_count": sum(goal["source"] == "FORMAL" for goal in goals),
                "candidate_goal_count": sum(goal["source"] == "CANDIDATE" for goal in goals),
                "scheduled_goal_total": self._money(total_remaining),
                "unscheduled_goals": [self._public_goal(goal) for goal in unscheduled],
            },
            "windows": windows,
            "suggestions": self._suggestions(
                status=status,
                windows=windows,
            ),
            "explanation": self._explanation(status, windows),
            "disclaimer": "系统不会依据家庭身份自动排序，也不会自动修改目标、预留资金或发起转账。",
        }

    def _candidate_change_set(
        self, owner_user_id: str, request_id: str | None
    ) -> LifeGraphChangeSet | None:
        if request_id is None:
            return None
        request = self.repository.get_change_request(owner_user_id, request_id)
        if request is None:
            raise LifeGoalConflictError("CHANGE_REQUEST_NOT_FOUND")
        if request["status"] != "PENDING":
            raise LifeGoalConflictError("CHANGE_REQUEST_NOT_PENDING")
        if hash_change_set(request["change_set"], request["schema_version"]) != request["snapshot_hash"]:
            raise LifeGoalConflictError("CHANGE_REQUEST_TAMPERED")
        try:
            return LifeGraphChangeSet.model_validate(request["change_set"])
        except ValidationError as exc:
            raise LifeGoalConflictError("CHANGE_SET_SCHEMA_INVALID") from exc

    def _collect_goals(
        self, graph: dict, candidate: LifeGraphChangeSet | None
    ) -> list[dict[str, Any]]:
        nodes = {node["node_id"]: node for node in graph["nodes"]}
        edges = list(graph["edges"])
        goals: list[dict[str, Any]] = []
        for goal in graph["goals"]:
            if goal["status"] in {"COMPLETED", "CANCELED"}:
                continue
            target = Decimal(goal["target_amount"])
            reserved = Decimal(goal["reserved_amount"])
            goals.append(self._goal_snapshot(
                goal_node_id=goal["goal_node_id"],
                label=nodes.get(goal["goal_node_id"], {}).get("display_label", "未命名目标"),
                target=target,
                reserved=reserved,
                currency=goal["currency"],
                due_at=goal["due_at"],
                source="FORMAL",
                nodes=nodes,
                edges=edges,
            ))

        if candidate is not None:
            candidate_nodes = {
                node.node_id: {
                    "node_id": node.node_id,
                    "display_label": node.display_label,
                    "node_type": node.node_type,
                }
                for node in candidate.nodes
            }
            nodes = nodes | candidate_nodes
            candidate_edges = [edge.model_dump(mode="json") for edge in candidate.edges]
            edges = edges + candidate_edges
            for goal in candidate.goals:
                goals.append(self._goal_snapshot(
                    goal_node_id=goal.goal_node_id,
                    label=nodes.get(goal.goal_node_id, {}).get("display_label", "未命名目标"),
                    target=goal.target_amount,
                    reserved=goal.reserved_amount,
                    currency=goal.currency,
                    due_at=goal.due_at.isoformat() if goal.due_at else None,
                    source="CANDIDATE",
                    nodes=nodes,
                    edges=edges,
                ))
        return goals

    @staticmethod
    def _goal_snapshot(
        *,
        goal_node_id: str,
        label: str,
        target: Decimal,
        reserved: Decimal,
        currency: str,
        due_at: str | None,
        source: str,
        nodes: dict[str, dict],
        edges: list[dict],
    ) -> dict[str, Any]:
        event_id = next(
            (
                edge["target_node_id"]
                for edge in edges
                if edge["source_node_id"] == goal_node_id
                and edge["relation_type"] == "TRIGGERED_BY"
            ),
            None,
        )
        person_id = next(
            (
                edge["source_node_id"]
                for edge in edges
                if edge["target_node_id"] == event_id
                and edge["relation_type"] == "HAS_EVENT"
            ),
            None,
        )
        due_date = datetime.fromisoformat(due_at).date() if due_at else None
        return {
            "goal_node_id": goal_node_id,
            "label": label,
            "related_person": nodes.get(person_id, {}).get("display_label") if person_id else None,
            "target_amount": target,
            "reserved_amount": reserved,
            "remaining_amount": max(target - reserved, Decimal("0.00")),
            "currency": currency,
            "due_date": due_date,
            "source": source,
        }

    def _financial_evidence(self, owner_user_id: str, as_of: date) -> tuple[Decimal | None, dict]:
        with Session(self.engine) as database:
            accounts = database.scalars(
                select(AccountRecord).where(AccountRecord.user_id == owner_user_id)
            ).all()
            transactions = database.scalars(
                select(TransactionRecord).where(TransactionRecord.user_id == owner_user_id)
            ).all()

        cny_accounts = [
            row.payload
            for row in accounts
            if row.payload.get("currency", "CNY") == "CNY"
        ]
        available_balance = (
            sum(
                (Decimal(str(account.get("available_balance", 0))) for account in cny_accounts),
                start=Decimal("0.00"),
            )
            if cny_accounts
            else None
        )
        window_start = as_of - timedelta(days=30)
        amounts: list[Decimal] = []
        for row in transactions:
            occurred_at = row.payload.get("occurred_at")
            try:
                occurred_date = datetime.fromisoformat(occurred_at).date()
            except (TypeError, ValueError):
                continue
            if window_start <= occurred_date <= as_of:
                amounts.append(Decimal(str(row.payload.get("amount", 0))))
        income = sum((amount for amount in amounts if amount > 0), start=Decimal("0.00"))
        expense = sum((-amount for amount in amounts if amount < 0), start=Decimal("0.00"))
        return available_balance, {
            "available": bool(amounts),
            "income": income,
            "expense": expense,
            "net": income - expense,
        }

    @staticmethod
    def _suggestions(
        *, status: str, windows: list[dict]
    ) -> list[dict[str, Any]]:
        if status == "CLEAR":
            return []
        affected_goal_ids = [
            goal["goal_node_id"]
            for window in windows
            if window["status"] != "CLEAR"
            for goal in window["goals"]
        ]
        suggestions = [
            {
                "code": "REVIEW_AMOUNT_OR_DATE",
                "description": "由用户选择调整一个或多个目标金额或日期，系统不预设家庭角色优先级。",
                "affected_goal_ids": affected_goal_ids,
                "requires_user_confirmation": True,
                "auto_apply": False,
            },
            {
                "code": "ADD_FUNDING_SOURCE",
                "description": "补充其他明确资金来源后重新计算，不将预计收入当作已到账资金。",
                "affected_goal_ids": affected_goal_ids,
                "requires_user_confirmation": True,
                "auto_apply": False,
            },
        ]
        if status == "PRESSURE":
            suggestions.append({
                "code": "USE_LIQUID_BALANCE",
                "description": "当前流动余额可能覆盖目标，但是否动用存量资金必须由用户明确确认。",
                "affected_goal_ids": affected_goal_ids,
                "requires_user_confirmation": True,
                "auto_apply": False,
            })
        return suggestions

    @staticmethod
    def _explanation(status: str, windows: list[dict]) -> str:
        if status == "CONFLICT":
            return "目标累计所需金额超过当前可支配余额，存在明确资金缺口。"
        if status == "PRESSURE":
            periods = "、".join(window["period"] for window in windows if window["status"] == "PRESSURE")
            return f"当前余额可以覆盖目标，但 {periods} 的目标金额超过近 30 天净现金流，可能挤压当期支出。"
        return "在现有余额和已观察现金流范围内，暂未发现目标资金冲突。"

    def _unknown_result(
        self,
        analysis_time: datetime,
        goals: list[dict[str, Any]],
        protected_balance: Decimal,
        reason: str,
        *,
        cash_flow: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "UNKNOWN",
            "severity": "UNKNOWN",
            "as_of": analysis_time.isoformat(),
            "currency": "CNY",
            "priority_decision": "USER_REQUIRED",
            "evidence": {
                "protected_balance": self._money(protected_balance),
                "cash_flow_evidence_available": bool(cash_flow and cash_flow["available"]),
                "goal_count": len(goals),
            },
            "windows": [],
            "suggestions": [],
            "explanation": f"缺少可靠数据，无法判断资金目标是否冲突：{reason}。",
            "disclaimer": "系统不会依据家庭身份自动排序，也不会自动修改目标、预留资金或发起转账。",
        }

    @staticmethod
    def _public_goal(goal: dict[str, Any]) -> dict[str, Any]:
        return {
            "goal_node_id": goal["goal_node_id"],
            "label": goal["label"],
            "related_person": goal["related_person"],
            "target_amount": LifeGoalConflictService._money(goal["target_amount"]),
            "reserved_amount": LifeGoalConflictService._money(goal["reserved_amount"]),
            "remaining_amount": LifeGoalConflictService._money(goal["remaining_amount"]),
            "currency": goal["currency"],
            "due_date": goal["due_date"].isoformat() if goal["due_date"] else None,
            "source": goal["source"],
        }

    @staticmethod
    def _money(value: Decimal | None) -> str | None:
        return format(value, ".2f") if value is not None else None

    @staticmethod
    def _add_months(value: date, months: int) -> date:
        month_index = value.year * 12 + value.month - 1 + months
        year, month_zero = divmod(month_index, 12)
        return date(year, month_zero + 1, 1) - timedelta(days=1)
