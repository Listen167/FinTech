"""生活关系图候选变更的严格结构定义。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Identifier = str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LifeGraphNodeCreate(StrictModel):
    node_id: Identifier = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    node_type: Literal["PERSON", "EVENT", "GOAL"]
    display_label: str = Field(min_length=1, max_length=100)
    sensitivity_level: Literal["PERSONAL", "SENSITIVE", "HIGH"] = "PERSONAL"
    attributes: dict[str, Any] = Field(default_factory=dict)


class LifeGraphEdgeCreate(StrictModel):
    edge_id: Identifier = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    source_node_id: Identifier = Field(min_length=1, max_length=64)
    target_node_id: Identifier = Field(min_length=1, max_length=64)
    relation_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    consent_scope: dict[str, Any] = Field(default_factory=dict)
    provenance: Literal["USER_DECLARED", "USER_CONFIRMED"] = "USER_CONFIRMED"
    confidence_bp: int = Field(default=10000, ge=0, le=10000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class LifeEventCreate(StrictModel):
    event_node_id: Identifier = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    occurs_at: datetime
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    recurrence_rule: str | None = Field(default=None, max_length=255)
    details: dict[str, Any] = Field(default_factory=dict)


class FinancialGoalCreate(StrictModel):
    goal_node_id: Identifier = Field(min_length=1, max_length=64)
    target_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    reserved_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    due_at: datetime | None = None
    funding_policy: dict[str, Any] = Field(default_factory=dict)


class EventActionCreate(StrictModel):
    action_id: Identifier = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    event_node_id: Identifier | None = Field(default=None, min_length=1, max_length=64)
    goal_node_id: Identifier | None = Field(default=None, min_length=1, max_length=64)
    action_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    scheduled_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_subject(self) -> "EventActionCreate":
        if self.event_node_id is None and self.goal_node_id is None:
            raise ValueError("ACTION_SUBJECT_REQUIRED")
        return self


class LifeGraphChangeSet(StrictModel):
    nodes: list[LifeGraphNodeCreate] = Field(default_factory=list, max_length=50)
    edges: list[LifeGraphEdgeCreate] = Field(default_factory=list, max_length=100)
    events: list[LifeEventCreate] = Field(default_factory=list, max_length=50)
    goals: list[FinancialGoalCreate] = Field(default_factory=list, max_length=50)
    actions: list[EventActionCreate] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_batch(self) -> "LifeGraphChangeSet":
        collections = {
            "node_id": [item.node_id for item in self.nodes],
            "edge_id": [item.edge_id for item in self.edges],
            "event_node_id": [item.event_node_id for item in self.events],
            "goal_node_id": [item.goal_node_id for item in self.goals],
            "action_id": [item.action_id for item in self.actions],
            "action_idempotency_key": [item.idempotency_key for item in self.actions],
        }
        if not any(collections.values()):
            raise ValueError("EMPTY_CHANGE_SET")
        for field_name, values in collections.items():
            if len(values) != len(set(values)):
                raise ValueError(f"DUPLICATE_{field_name.upper()}")
        return self


class CreateLifeGraphChangeRequest(StrictModel):
    summary: str = Field(min_length=1, max_length=500)
    change_set: LifeGraphChangeSet
    source_run_id: Identifier | None = Field(default=None, min_length=1, max_length=64)


class LifeGraphDecisionRequest(StrictModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=255)


class LifeGraphNodeCorrection(StrictModel):
    display_label: str | None = Field(default=None, min_length=1, max_length=100)
    event_date: date | None = None
    target_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )
    reason: str = Field(min_length=2, max_length=255)

    @field_validator("display_label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "LifeGraphNodeCorrection":
        if (
            self.display_label is None
            and self.event_date is None
            and self.target_amount is None
        ):
            raise ValueError("CORRECTION_CHANGE_REQUIRED")
        return self
