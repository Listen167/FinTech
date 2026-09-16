from enum import StrEnum
from pydantic import BaseModel


class PolicyLevel(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    BLOCKED = "BLOCKED"


class PolicyDecision(BaseModel):
    level: PolicyLevel
    action: str
    reason: str
    risk_score: int = 0


def evaluate_operation(operation: str, amount: float | None = None) -> PolicyDecision:
    if operation == "transfer" and amount is not None:
        if amount > 1000:
            return PolicyDecision(
                level=PolicyLevel.RED,
                action="REQUIRE_STRONG_VERIFICATION",
                reason="Transfer amount exceeds strong verification threshold.",
                risk_score=80,
            )

        return PolicyDecision(
            level=PolicyLevel.YELLOW,
            action="REQUIRE_CONFIRMATION",
            reason="Transfer requires explicit user confirmation.",
            risk_score=45,
        )

    return PolicyDecision(
        level=PolicyLevel.GREEN,
        action="AUTO_EXECUTE",
        reason="Read-only or low-risk operation.",
        risk_score=10,
    )

