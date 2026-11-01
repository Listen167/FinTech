"""家庭守护圈邀请、授权和双确认请求契约。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


GuardianScenario = Literal["LARGE_TRANSFER", "NEW_RECIPIENT", "SUSPECTED_FRAUD"]
GuardianVisibleField = Literal[
    "RISK_LEVEL",
    "SCENARIO",
    "AMOUNT_BAND",
    "RECIPIENT_MASKED",
    "RISK_SUMMARY",
    "OCCURRED_AT",
]


class StrictGuardianModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateGuardianInvitationRequest(StrictGuardianModel):
    guardian_user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")
    scenarios: list[GuardianScenario] = Field(min_length=1, max_length=3)
    amount_lower_bound: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    amount_upper_bound: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    visible_fields: list[GuardianVisibleField] = Field(
        default_factory=lambda: ["RISK_LEVEL", "SCENARIO", "AMOUNT_BAND", "RISK_SUMMARY"],
        min_length=1,
        max_length=6,
    )
    consent_valid_until: datetime

    @model_validator(mode="after")
    def validate_scope(self) -> "CreateGuardianInvitationRequest":
        if self.amount_upper_bound < self.amount_lower_bound:
            raise ValueError("amount_upper_bound 不能小于 amount_lower_bound")
        if len(set(self.scenarios)) != len(self.scenarios):
            raise ValueError("scenarios 不能重复")
        if len(set(self.visible_fields)) != len(self.visible_fields):
            raise ValueError("visible_fields 不能重复")
        return self


class GuardianInvitationTokenRequest(StrictGuardianModel):
    token: str = Field(min_length=32, max_length=256)
    reason: str | None = Field(default=None, min_length=2, max_length=500)


class GuardianReasonRequest(StrictGuardianModel):
    reason: str = Field(min_length=2, max_length=500)


class CreateGuardianRiskReviewRequest(StrictGuardianModel):
    consent_id: str = Field(pattern=r"^guardian_consent_[A-Za-z0-9]{8,32}$")
    scenario: GuardianScenario
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    risk_level: Literal["MEDIUM", "HIGH", "CRITICAL"]
    recipient_masked: str | None = Field(default=None, min_length=2, max_length=64)
    risk_summary: str = Field(min_length=5, max_length=240)
    source_run_id: str | None = Field(default=None, pattern=r"^run_[A-Za-z0-9_-]{4,100}$")
    expires_in_seconds: int = Field(default=900, ge=120, le=3600)


class GuardianOwnerDecisionRequest(StrictGuardianModel):
    confirmed: bool
    note: str | None = Field(default=None, min_length=2, max_length=500)


class GuardianRiskDecisionRequest(StrictGuardianModel):
    risk_acknowledged: bool
    note: str | None = Field(default=None, min_length=2, max_length=500)
