"""家庭资金目标协商的严格数据契约。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Money = Annotated[Decimal, Field(ge=Decimal("0.00"), max_digits=14, decimal_places=2)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceSource(str, Enum):
    USER_CONFIRMED = "USER_CONFIRMED"
    BANK_CONFIRMED = "BANK_CONFIRMED"
    MODEL_INFERRED = "MODEL_INFERRED"
    FORECAST = "FORECAST"


class GoalRigidity(str, Enum):
    RIGID = "RIGID"
    FLEXIBLE = "FLEXIBLE"


class PlanFeasibility(str, Enum):
    GUARANTEED = "GUARANTEED"
    CONDITIONAL = "CONDITIONAL"
    INFEASIBLE = "INFEASIBLE"


class FundingPlanType(str, Enum):
    BASELINE = "BASELINE"
    REDUCE_FLEXIBLE = "REDUCE_FLEXIBLE"
    DELAY_FLEXIBLE = "DELAY_FLEXIBLE"
    INSTALLMENT = "INSTALLMENT"
    WAIT_CONFIRMED_INCOME = "WAIT_CONFIRMED_INCOME"
    CUSTOM = "CUSTOM"


class ConstraintEvidence(StrictModel):
    field_name: str = Field(min_length=1, max_length=64)
    source: EvidenceSource
    confidence_bp: int = Field(ge=0, le=10000)
    reference_id: str | None = Field(default=None, max_length=128)
    captured_at: datetime | None = None


class GoalConstraintInput(StrictModel):
    goal_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    target_amount: Money
    minimum_amount: Money
    reserved_amount: Money = Decimal("0.00")
    due_date: date
    rigidity: GoalRigidity
    can_reduce: bool = False
    can_delay: bool = False
    can_installments: bool = False
    priority_rank: int | None = Field(default=None, ge=1, le=100)
    relationship_context: str | None = Field(default=None, max_length=100)
    evidence: list[ConstraintEvidence] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_amounts(self) -> "GoalConstraintInput":
        if self.minimum_amount > self.target_amount:
            raise ValueError("minimum_amount 不能大于 target_amount")
        if self.reserved_amount > self.target_amount:
            raise ValueError("reserved_amount 不能大于 target_amount")
        if self.rigidity == GoalRigidity.RIGID and self.minimum_amount != self.target_amount:
            raise ValueError("刚性目标的 minimum_amount 必须等于 target_amount")
        return self


class CommittedExpense(StrictModel):
    expense_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    amount: Money
    due_date: date
    evidence: ConstraintEvidence


class IncomeEvidence(StrictModel):
    income_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    amount: Money
    available_on: date
    source: EvidenceSource
    confidence_bp: int = Field(ge=0, le=10000)
    reference_id: str | None = Field(default=None, max_length=128)

    @property
    def is_guaranteed(self) -> bool:
        return self.source in {
            EvidenceSource.USER_CONFIRMED,
            EvidenceSource.BANK_CONFIRMED,
        }


class GlobalFundingConstraints(StrictModel):
    starting_balance: Money
    protected_balance: Money = Decimal("0.00")
    allowed_account_ids: list[str] = Field(min_length=1, max_length=20)
    planning_start: date
    planning_end: date
    committed_expenses: list[CommittedExpense] = Field(default_factory=list, max_length=100)
    incomes: list[IncomeEvidence] = Field(default_factory=list, max_length=100)
    evidence: list[ConstraintEvidence] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_range(self) -> "GlobalFundingConstraints":
        if self.planning_end < self.planning_start:
            raise ValueError("planning_end 不能早于 planning_start")
        return self


class FundingPlanningRequest(StrictModel):
    goals: list[GoalConstraintInput] = Field(min_length=1, max_length=50)
    constraints: GlobalFundingConstraints

    @model_validator(mode="after")
    def validate_unique_goals_and_dates(self) -> "FundingPlanningRequest":
        ids = [goal.goal_id for goal in self.goals]
        if len(ids) != len(set(ids)):
            raise ValueError("goal_id 不能重复")
        outside = [
            goal.goal_id
            for goal in self.goals
            if not self.constraints.planning_start <= goal.due_date <= self.constraints.planning_end
        ]
        if outside:
            raise ValueError(f"目标截止日期超出规划范围: {','.join(outside)}")
        return self


class GoalAdjustment(StrictModel):
    goal_id: str
    target_amount: Money | None = None
    due_date: date | None = None
    funding_policy_patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=255)


class TimelineAllocation(StrictModel):
    goal_id: str
    label: str
    amount: Money
    rigidity: GoalRigidity


class CashFlowTimelinePoint(StrictModel):
    date: date
    opening_balance: Decimal
    confirmed_income: Money
    conditional_income: Money
    committed_expenses: Money
    goal_allocations: list[TimelineAllocation]
    closing_guaranteed_balance: Decimal
    closing_conditional_balance: Decimal
    protected_balance: Money
    guaranteed_gap: Money


class FundingPlan(StrictModel):
    plan_id: str
    plan_type: FundingPlanType
    feasibility: PlanFeasibility
    title: str
    explanation: str
    adjustments: list[GoalAdjustment]
    timeline: list[CashFlowTimelinePoint]
    shortfall: Money
    required_confirmed_income: Money
    income_evidence: list[IncomeEvidence]
    affected_goal_ids: list[str]
    formula: str
    warnings: list[str] = Field(default_factory=list)
    model_explanation: str | None = None


class FundingPlanSet(StrictModel):
    generated_at: datetime
    priority_decision: Literal["USER_REQUIRED"] = "USER_REQUIRED"
    relationship_used_for_priority: Literal[False] = False
    plans: list[FundingPlan]
    invalid_suggestion_codes: list[str] = Field(default_factory=list)


class CreateFundingNegotiationRequest(StrictModel):
    request_text: str = Field(min_length=1, max_length=2000)
    planning: FundingPlanningRequest | None = None
    source_run_id: str | None = Field(default=None, max_length=64)


class SelectFundingPlanRequest(StrictModel):
    plan_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class CompleteFundingConstraintsRequest(StrictModel):
    planning: FundingPlanningRequest
    reason: str = Field(min_length=1, max_length=500)


class ProposeCustomFundingPlanRequest(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    model_explanation: str = Field(min_length=1, max_length=1000)
    adjustments: list[GoalAdjustment] = Field(min_length=1, max_length=50)


class FundingDecisionRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=500)


class CreateFundingConsentScopeRequest(StrictModel):
    goal_node_ids: list[str] = Field(min_length=1, max_length=50)
    amount_lower_bound: Money
    amount_upper_bound: Money
    protected_balance: Money
    max_single_adjustment: Money
    valid_from: datetime
    valid_until: datetime
    auto_apply_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_scope(self) -> "CreateFundingConsentScopeRequest":
        if self.amount_upper_bound < self.amount_lower_bound:
            raise ValueError("amount_upper_bound 不能小于 amount_lower_bound")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until 必须晚于 valid_from")
        return self
