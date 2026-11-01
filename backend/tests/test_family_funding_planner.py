from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.app.schemas.family_funding import (
    EvidenceSource,
    FundingPlanningRequest,
    GlobalFundingConstraints,
    GoalConstraintInput,
    GoalRigidity,
    IncomeEvidence,
    PlanFeasibility,
)
from backend.app.services.family_funding_planner import FamilyFundingPlanner


def goal(
    goal_id: str,
    amount: str,
    due: date,
    *,
    rigid: bool,
    minimum: str | None = None,
    reduce: bool = False,
    delay: bool = False,
    installments: bool = False,
    relationship: str | None = None,
) -> GoalConstraintInput:
    return GoalConstraintInput(
        goal_id=goal_id,
        label=goal_id,
        target_amount=amount,
        minimum_amount=minimum or amount,
        due_date=due,
        rigidity=GoalRigidity.RIGID if rigid else GoalRigidity.FLEXIBLE,
        can_reduce=reduce,
        can_delay=delay,
        can_installments=installments,
        relationship_context=relationship,
    )


def planning(
    goals: list[GoalConstraintInput],
    *,
    balance: str = "10000.00",
    protected: str = "0.00",
    incomes: list[IncomeEvidence] | None = None,
) -> FundingPlanningRequest:
    return FundingPlanningRequest(
        goals=goals,
        constraints=GlobalFundingConstraints(
            starting_balance=balance,
            protected_balance=protected,
            allowed_account_ids=["acct_001"],
            planning_start=date(2026, 11, 1),
            planning_end=date(2027, 3, 31),
            incomes=incomes or [],
        ),
    )


def test_10000_tuition_8000_gift_3000_has_guaranteed_and_conditional_options():
    request = planning([
        goal("tuition", "8000", date(2026, 11, 20), rigid=True, relationship="女儿"),
        goal(
            "gift",
            "3000",
            date(2026, 11, 30),
            rigid=False,
            minimum="1000",
            reduce=True,
            delay=True,
            installments=True,
            relationship="妻子",
        ),
    ])

    result = FamilyFundingPlanner().build_plans(
        request, generated_at=datetime(2026, 11, 1, 9, 0, 0)
    )
    reduction = next(plan for plan in result.plans if plan.plan_type == "REDUCE_FLEXIBLE")
    delayed = next(plan for plan in result.plans if plan.plan_type == "WAIT_CONFIRMED_INCOME")

    assert reduction.feasibility == PlanFeasibility.GUARANTEED
    assert reduction.adjustments[0].goal_id == "gift"
    assert reduction.adjustments[0].target_amount == Decimal("2000")
    assert delayed.feasibility == PlanFeasibility.CONDITIONAL
    assert delayed.required_confirmed_income == Decimal("1000")
    assert result.priority_decision == "USER_REQUIRED"
    assert result.relationship_used_for_priority is False


def test_confirmed_income_is_available_only_on_its_arrival_date():
    salary = IncomeEvidence(
        income_id="salary",
        label="已确认工资",
        amount="3000",
        available_on=date(2026, 12, 1),
        source=EvidenceSource.BANK_CONFIRMED,
        confidence_bp=10000,
    )
    request = planning(
        [goal("early", "11000", date(2026, 11, 20), rigid=True)],
        incomes=[salary],
    )

    baseline = FamilyFundingPlanner().build_plans(request).plans[0]

    assert baseline.feasibility == PlanFeasibility.INFEASIBLE
    assert baseline.timeline[0].guaranteed_gap == Decimal("1000")
    assert baseline.timeline[-1].confirmed_income == Decimal("3000")


def test_forecast_income_never_makes_a_guaranteed_plan():
    forecast = IncomeEvidence(
        income_id="bonus",
        label="预计奖金",
        amount="3000",
        available_on=date(2026, 11, 10),
        source=EvidenceSource.FORECAST,
        confidence_bp=6500,
    )
    request = planning(
        [goal("tuition", "12000", date(2026, 11, 20), rigid=True)],
        incomes=[forecast],
    )

    baseline = FamilyFundingPlanner().build_plans(request).plans[0]

    assert baseline.feasibility == PlanFeasibility.CONDITIONAL
    assert baseline.shortfall == Decimal("2000")
    assert baseline.timeline[-1].conditional_income == Decimal("0") or any(
        point.conditional_income == Decimal("3000") for point in baseline.timeline
    )


def test_protected_balance_is_always_preserved_by_guaranteed_plan():
    request = planning(
        [goal("gift", "3000", date(2026, 11, 30), rigid=False, minimum="1000", reduce=True)],
        protected="8000",
    )

    result = FamilyFundingPlanner().build_plans(request)
    reduction = next(plan for plan in result.plans if plan.plan_type == "REDUCE_FLEXIBLE")

    assert reduction.feasibility == PlanFeasibility.GUARANTEED
    assert reduction.adjustments[0].target_amount == Decimal("2000")
    assert reduction.timeline[-1].closing_guaranteed_balance == Decimal("8000")


def test_two_rigid_goals_cannot_be_silently_reprioritized_or_reduced():
    request = planning([
        goal("daughter", "8000", date(2026, 11, 20), rigid=True, relationship="女儿"),
        goal("spouse", "3000", date(2026, 11, 30), rigid=True, relationship="妻子"),
    ])

    result = FamilyFundingPlanner().build_plans(request)

    assert result.plans[0].feasibility == PlanFeasibility.INFEASIBLE
    assert all(plan.plan_type != "REDUCE_FLEXIBLE" for plan in result.plans)


def test_invalid_custom_plan_below_minimum_is_rejected():
    request = planning([
        goal("gift", "3000", date(2026, 11, 30), rigid=False, minimum="1000", reduce=True)
    ])
    adjustment = {
        "goal_id": "gift",
        "target_amount": "500",
        "reason": "模型给出的非法建议",
    }

    with pytest.raises(ValueError, match="PLAN_BELOW_GOAL_MINIMUM"):
        from backend.app.schemas.family_funding import GoalAdjustment

        FamilyFundingPlanner().validate_custom_plan(
            request, [GoalAdjustment.model_validate(adjustment)]
        )
