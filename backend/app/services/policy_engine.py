from enum import Enum
from pydantic import BaseModel, Field


POLICY_VERSION = "risk-policy-2.0.0"


class PolicyLevel(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    BLOCKED = "BLOCKED"


class PolicyDecision(BaseModel):
    level: PolicyLevel
    action: str
    reason: str
    risk_score: int = 0
    risk_factors: list[str] = Field(default_factory=list)


def evaluate_operation(
    operation: str,
    amount: float | None = None,
    context: dict | None = None,
) -> PolicyDecision:
    """确定性动态分级；模型只能提供 risk_hints，不能降低计算结果。"""

    context = context or {}
    if operation in {"bill.analyze", "subscription.list", "account.balance.get"}:
        return PolicyDecision(
            level=PolicyLevel.GREEN,
            action="AUTO_EXECUTE",
            reason="只读操作，可自动执行。",
            risk_score=10,
            risk_factors=["READ_ONLY"],
        )

    if operation == "subscription.cancel":
        return PolicyDecision(
            level=PolicyLevel.YELLOW,
            action="REQUIRE_CONFIRMATION",
            reason="取消订阅会改变后续扣费状态，需要用户明确确认。",
            risk_score=45,
            risk_factors=["STATE_CHANGE"],
        )

    if operation == "life.event_plan":
        return PolicyDecision(
            level=PolicyLevel.YELLOW,
            action="REQUIRE_CONFIRMATION",
            reason="生活图谱候选会写入关系、事件和资金目标，需要用户核对后确认。",
            risk_score=40,
            risk_factors=["LIFE_GRAPH_WRITE", "USER_PRIORITY_REQUIRED"],
        )

    if operation == "transfer.create" and amount is not None:
        score = 35
        factors = ["MONEY_MOVEMENT"]
        hints = context.get("risk_hints", [])
        if amount > 1000:
            score += 40
            factors.append("AMOUNT_OVER_1000")
        if context.get("new_recipient"):
            score += 15
            factors.append("NEW_RECIPIENT")
        if hints:
            score += min(45, len(hints) * 18)
            factors.extend(hints)

        if score >= 95:
            return PolicyDecision(
                level=PolicyLevel.BLOCKED,
                action="BLOCK",
                reason="操作同时命中高危诈骗语义和交易风险，已触发安全熔断。",
                risk_score=min(score, 100),
                risk_factors=factors,
            )
        if score >= 70:
            return PolicyDecision(
                level=PolicyLevel.RED,
                action="REQUIRE_STRONG_VERIFICATION",
                reason="大额、新收款人或可疑语义使本次转账升级为强验证。",
                risk_score=min(score, 100),
                risk_factors=factors,
            )
        return PolicyDecision(
            level=PolicyLevel.YELLOW,
            action="REQUIRE_CONFIRMATION",
            reason="资金操作需要核对收款人和金额后明确确认。",
            risk_score=score,
            risk_factors=factors,
        )

    return PolicyDecision(
        level=PolicyLevel.BLOCKED,
        action="BLOCK",
        reason="未登记的写操作默认拒绝。",
        risk_score=100,
        risk_factors=["UNKNOWN_OPERATION"],
    )

