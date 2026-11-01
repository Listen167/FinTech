"""AI 意图的严格结构化契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


IntentName = Literal[
    "bill.analyze",
    "subscription.list",
    "subscription.cancel",
    "transfer.create",
    "finance.cross_scenario",
    "life.event_plan",
    "unknown",
]


class IntentSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient: str | None = None
    amount: float | None = Field(default=None, gt=0)
    currency: str = "CNY"
    keyword: str | None = None
    period: str | None = None
    detect_abnormal: bool | None = None
    include_bill: bool | None = None
    include_subscriptions: bool | None = None
    cancel_subscription: bool | None = None
    include_transfer: bool | None = None
    subscription_keyword: str | None = None
    related_person: str | None = None
    relation_type: str | None = None
    event_type: str | None = None
    event_label: str | None = None
    event_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    event_year: int | None = Field(default=None, ge=2000, le=2200)
    event_month: int | None = Field(default=None, ge=1, le=12)
    recurrence_rule: str | None = None
    goal_required: bool | None = None
    goal_label: str | None = None
    target_amount: float | None = Field(default=None, gt=0)
    reminder_days_before: int | None = Field(default=None, ge=0, le=365)


class StructuredIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: IntentName
    confidence: float = Field(ge=0, le=1)
    slots: IntentSlots = Field(default_factory=IntentSlots)
    missing_slots: list[str] = Field(default_factory=list)
    risk_hints: list[str] = Field(default_factory=list)


class ModelTrace(BaseModel):
    provider: str = "deepseek"
    requested_model: str = "deepseek-chat"
    response_model: str | None = None
    status: Literal["DISABLED", "SUCCEEDED", "TIMEOUT", "API_ERROR", "INVALID_OUTPUT"]
    latency_ms: int = 0
    fallback_used: bool = False
    error_type: str | None = None
    request_id: str | None = None
