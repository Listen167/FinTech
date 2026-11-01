"""可复现的家庭资金时间线与协商方案生成器。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

from backend.app.schemas.family_funding import (
    CashFlowTimelinePoint,
    EvidenceSource,
    FundingPlan,
    FundingPlanSet,
    FundingPlanType,
    FundingPlanningRequest,
    GoalAdjustment,
    GoalConstraintInput,
    GoalRigidity,
    PlanFeasibility,
    TimelineAllocation,
)


ZERO = Decimal("0.00")


class FamilyFundingPlanner:
    """只使用金额、期限、刚柔性和用户显式优先级，不使用人物关系排序。"""

    version = "family-funding-planner/1.0"

    def build_plans(
        self,
        request: FundingPlanningRequest,
        *,
        generated_at: datetime | None = None,
    ) -> FundingPlanSet:
        now = generated_at or datetime.now(timezone.utc)
        baseline = self._evaluate(
            request,
            plan_type=FundingPlanType.BASELINE,
            title="保持所有目标",
            explanation="不调整任何目标，直接检验当前资金约束。",
            adjustments=[],
        )
        plans = [baseline]
        if baseline.feasibility != PlanFeasibility.GUARANTEED:
            reduced = self._build_reduction_plan(request, baseline.shortfall)
            if reduced is not None:
                plans.append(reduced)
            delayed = self._build_delay_plan(request)
            if delayed is not None:
                plans.append(delayed)
            installment = self._build_installment_plan(request)
            if installment is not None:
                plans.append(installment)
        return FundingPlanSet(generated_at=now, plans=self._deduplicate(plans))

    def validate_custom_plan(
        self,
        request: FundingPlanningRequest,
        adjustments: list[GoalAdjustment],
        *,
        title: str = "模型建议方案",
    ) -> FundingPlan:
        known = {goal.goal_id for goal in request.goals}
        if any(item.goal_id not in known for item in adjustments):
            raise ValueError("MODEL_PLAN_UNKNOWN_GOAL")
        return self._evaluate(
            request,
            plan_type=FundingPlanType.CUSTOM,
            title=title,
            explanation="模型仅提出候选调整；以下结果已由确定性算法重新计算。",
            adjustments=adjustments,
        )

    def _build_reduction_plan(
        self,
        request: FundingPlanningRequest,
        baseline_shortfall: Decimal,
    ) -> FundingPlan | None:
        remaining = baseline_shortfall
        adjustments: list[GoalAdjustment] = []
        flexible = sorted(
            (goal for goal in request.goals if goal.can_reduce),
            key=lambda item: (
                item.priority_rank if item.priority_rank is not None else 101,
                item.due_date,
                item.goal_id,
            ),
            reverse=True,
        )
        for goal in flexible:
            reducible = max(goal.target_amount - goal.minimum_amount, ZERO)
            reduction = min(reducible, remaining)
            if reduction <= ZERO:
                continue
            adjustments.append(GoalAdjustment(
                goal_id=goal.goal_id,
                target_amount=goal.target_amount - reduction,
                reason=f"在不低于最低金额的前提下缩减 {self._money(reduction)} 元",
            ))
            remaining -= reduction
            if remaining <= ZERO:
                break
        if not adjustments:
            return None
        plan = self._evaluate(
            request,
            plan_type=FundingPlanType.REDUCE_FLEXIBLE,
            title="优先守住刚性目标，缩减弹性目标",
            explanation="保留备用金与刚性最低金额，仅压缩用户允许缩减的弹性目标。",
            adjustments=adjustments,
        )
        return plan

    def _build_delay_plan(self, request: FundingPlanningRequest) -> FundingPlan | None:
        candidates = sorted(
            (goal for goal in request.goals if goal.can_delay and goal.rigidity == GoalRigidity.FLEXIBLE),
            key=lambda item: (item.due_date, item.priority_rank or 101, item.goal_id),
            reverse=True,
        )
        if not candidates:
            return None
        goal = candidates[0]
        delayed_to = min(goal.due_date + timedelta(days=30), request.constraints.planning_end)
        if delayed_to <= goal.due_date:
            return None
        return self._evaluate(
            request,
            plan_type=FundingPlanType.WAIT_CONFIRMED_INCOME,
            title="延期弹性目标并等待确认收入",
            explanation="先满足较早到期的刚性目标；延期部分只有在收入被用户或银行确认后才可执行。",
            adjustments=[GoalAdjustment(
                goal_id=goal.goal_id,
                due_date=delayed_to,
                funding_policy_patch={"condition": "WAIT_CONFIRMED_INCOME"},
                reason=f"将截止日期延至 {delayed_to.isoformat()} 并等待可靠收入",
            )],
            force_conditional=True,
        )

    def _build_installment_plan(self, request: FundingPlanningRequest) -> FundingPlan | None:
        candidates = sorted(
            (
                goal for goal in request.goals
                if goal.can_installments and goal.rigidity == GoalRigidity.FLEXIBLE
            ),
            key=lambda item: (item.due_date, item.priority_rank or 101, item.goal_id),
            reverse=True,
        )
        if not candidates:
            return None
        goal = candidates[0]
        installment = max(goal.minimum_amount, (goal.target_amount / 2).quantize(Decimal("0.01")))
        return self._evaluate(
            request,
            plan_type=FundingPlanType.INSTALLMENT,
            title="将弹性目标拆分为两期",
            explanation="当前只承诺首期金额，余款需在后续收入确认后重新校验。",
            adjustments=[GoalAdjustment(
                goal_id=goal.goal_id,
                target_amount=installment,
                funding_policy_patch={
                    "mode": "INSTALLMENTS",
                    "remaining_amount": self._money(goal.target_amount - installment),
                    "requires_recalculation": True,
                },
                reason="把当前承诺金额调整为首期，余款不计入保证方案",
            )],
            force_conditional=True,
        )

    def _evaluate(
        self,
        request: FundingPlanningRequest,
        *,
        plan_type: FundingPlanType,
        title: str,
        explanation: str,
        adjustments: list[GoalAdjustment],
        force_conditional: bool = False,
    ) -> FundingPlan:
        goals = self._adjusted_goals(request.goals, adjustments)
        timeline, shortfall = self._timeline(request, goals)
        conditional_income = sum(
            (income.amount for income in request.constraints.incomes if not income.is_guaranteed),
            start=ZERO,
        )
        if shortfall <= ZERO and not force_conditional:
            feasibility = PlanFeasibility.GUARANTEED
        elif shortfall <= conditional_income or force_conditional:
            feasibility = PlanFeasibility.CONDITIONAL
        else:
            feasibility = PlanFeasibility.INFEASIBLE
        required = shortfall if feasibility != PlanFeasibility.GUARANTEED else ZERO
        warnings: list[str] = []
        if feasibility == PlanFeasibility.CONDITIONAL:
            warnings.append("条件方案不能视为资金保证，执行前必须重新确认收入与余额。")
        if feasibility == PlanFeasibility.INFEASIBLE:
            warnings.append("即使计入当前预测收入，方案仍存在资金缺口。")
        affected = sorted({item.goal_id for item in adjustments})
        plan_id = self._plan_id(plan_type, adjustments)
        return FundingPlan(
            plan_id=plan_id,
            plan_type=plan_type,
            feasibility=feasibility,
            title=title,
            explanation=explanation,
            adjustments=adjustments,
            timeline=timeline,
            shortfall=shortfall,
            required_confirmed_income=required,
            income_evidence=request.constraints.incomes,
            affected_goal_ids=affected,
            formula="期末保证余额 = 期初余额 + 已确认收入 - 承诺支出 - 到期目标分配；且期末保证余额不得低于备用金",
            warnings=warnings,
        )

    def _timeline(
        self,
        request: FundingPlanningRequest,
        goals: list[GoalConstraintInput],
    ) -> tuple[list[CashFlowTimelinePoint], Decimal]:
        constraints = request.constraints
        dates = sorted({
            *(goal.due_date for goal in goals),
            *(item.due_date for item in constraints.committed_expenses),
            *(item.available_on for item in constraints.incomes),
        })
        guaranteed_balance = constraints.starting_balance
        conditional_balance = constraints.starting_balance
        max_gap = ZERO
        timeline: list[CashFlowTimelinePoint] = []
        goals_by_date: dict[date, list[GoalConstraintInput]] = {}
        for goal in goals:
            goals_by_date.setdefault(goal.due_date, []).append(goal)
        expenses_by_date = {
            day: sum(
                (item.amount for item in constraints.committed_expenses if item.due_date == day),
                start=ZERO,
            )
            for day in dates
        }
        incomes_by_date = {day: [item for item in constraints.incomes if item.available_on == day] for day in dates}

        for day in dates:
            opening = guaranteed_balance
            confirmed_income = sum(
                (item.amount for item in incomes_by_date[day] if item.is_guaranteed),
                start=ZERO,
            )
            forecast_income = sum(
                (item.amount for item in incomes_by_date[day] if not item.is_guaranteed),
                start=ZERO,
            )
            expenses = expenses_by_date[day]
            ordered_goals = sorted(
                goals_by_date.get(day, []),
                key=lambda item: (
                    0 if item.rigidity == GoalRigidity.RIGID else 1,
                    item.priority_rank if item.priority_rank is not None else 101,
                    item.goal_id,
                ),
            )
            allocations = [
                TimelineAllocation(
                    goal_id=goal.goal_id,
                    label=goal.label,
                    amount=max(goal.target_amount - goal.reserved_amount, ZERO),
                    rigidity=goal.rigidity,
                )
                for goal in ordered_goals
            ]
            allocated = sum((item.amount for item in allocations), start=ZERO)
            guaranteed_balance += confirmed_income - expenses - allocated
            conditional_balance += confirmed_income + forecast_income - expenses - allocated
            gap = max(constraints.protected_balance - guaranteed_balance, ZERO)
            max_gap = max(max_gap, gap)
            timeline.append(CashFlowTimelinePoint(
                date=day,
                opening_balance=opening,
                confirmed_income=confirmed_income,
                conditional_income=forecast_income,
                committed_expenses=expenses,
                goal_allocations=allocations,
                closing_guaranteed_balance=guaranteed_balance,
                closing_conditional_balance=conditional_balance,
                protected_balance=constraints.protected_balance,
                guaranteed_gap=gap,
            ))
        return timeline, max_gap

    @staticmethod
    def _adjusted_goals(
        goals: list[GoalConstraintInput],
        adjustments: list[GoalAdjustment],
    ) -> list[GoalConstraintInput]:
        by_id = {item.goal_id: item for item in adjustments}
        result: list[GoalConstraintInput] = []
        for goal in goals:
            change = by_id.get(goal.goal_id)
            data = goal.model_dump()
            if change is not None and change.target_amount is not None:
                if change.target_amount < goal.minimum_amount:
                    raise ValueError("PLAN_BELOW_GOAL_MINIMUM")
                if change.target_amount < goal.reserved_amount:
                    raise ValueError("PLAN_BELOW_RESERVED_AMOUNT")
                if change.target_amount > goal.target_amount:
                    raise ValueError("PLAN_INCREASE_NOT_ALLOWED")
                data["target_amount"] = change.target_amount
            if change is not None and change.due_date is not None:
                if change.due_date < goal.due_date or not goal.can_delay:
                    raise ValueError("PLAN_DELAY_NOT_ALLOWED")
                data["due_date"] = change.due_date
            result.append(GoalConstraintInput.model_validate(data))
        return result

    @staticmethod
    def _plan_id(plan_type: FundingPlanType, adjustments: list[GoalAdjustment]) -> str:
        body = "|".join(
            f"{item.goal_id}:{item.target_amount}:{item.due_date}:{sorted(item.funding_policy_patch.items())}"
            for item in adjustments
        )
        return f"plan_{sha256(f'{plan_type.value}|{body}'.encode()).hexdigest()[:16]}"

    @staticmethod
    def _deduplicate(plans: list[FundingPlan]) -> list[FundingPlan]:
        unique: dict[str, FundingPlan] = {}
        for plan in plans:
            unique.setdefault(plan.plan_id, plan)
        return list(unique.values())

    @staticmethod
    def _money(value: Decimal) -> str:
        return format(value, ".2f")
