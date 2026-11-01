"""家庭资金协商 LangGraph，只编排状态，不绕过服务层写入。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.schemas.family_funding import FundingPlanningRequest
from backend.app.services.family_funding_planner import FamilyFundingPlanner


FundingWorkflowEvent = Literal[
    "CREATE", "SELECT", "CONFIRM", "HANDOFF", "REJECT", "CANCEL", "UNDO"
]


class FamilyFundingWorkflowState(TypedDict, total=False):
    event: FundingWorkflowEvent
    planning: dict
    missing_fields: list[str]
    plans: list[dict]
    selected_plan_id: str
    stage: str
    trace: list[str]
    message: str
    error_code: str | None


class FamilyFundingWorkflow:
    engine_name = "langgraph"
    version = 1

    def __init__(self, planner: FamilyFundingPlanner | None = None) -> None:
        self.planner = planner or FamilyFundingPlanner()
        builder = StateGraph(FamilyFundingWorkflowState)
        builder.add_node("load_context", self._load_context)
        builder.add_node("complete_constraints", self._complete_constraints)
        builder.add_node("await_input", self._await_input)
        builder.add_node("build_timeline", self._build_timeline)
        builder.add_node("calculate_plans", self._calculate_plans)
        builder.add_node("await_selection", self._await_selection)
        builder.add_node("create_candidate", self._create_candidate)
        builder.add_node("await_confirmation", self._await_confirmation)
        builder.add_node("apply_confirmation", self._apply_confirmation)
        builder.add_node("human_handoff", self._human_handoff)
        builder.add_node("reject", self._reject)
        builder.add_node("cancel", self._cancel)
        builder.add_node("undo", self._undo)
        builder.add_conditional_edges(
            START,
            self._entry_route,
            {
                "create": "load_context",
                "select": "create_candidate",
                "confirm": "apply_confirmation",
                "handoff": "human_handoff",
                "reject": "reject",
                "cancel": "cancel",
                "undo": "undo",
            },
        )
        builder.add_edge("load_context", "complete_constraints")
        builder.add_conditional_edges(
            "complete_constraints",
            lambda state: "missing" if state.get("missing_fields") else "complete",
            {"missing": "await_input", "complete": "build_timeline"},
        )
        builder.add_edge("build_timeline", "calculate_plans")
        builder.add_edge("calculate_plans", "await_selection")
        builder.add_edge("create_candidate", "await_confirmation")
        for node in (
            "await_input", "await_selection", "await_confirmation", "apply_confirmation",
            "human_handoff", "reject", "cancel", "undo",
        ):
            builder.add_edge(node, END)
        self.graph = builder.compile()

    def invoke(self, state: FamilyFundingWorkflowState) -> FamilyFundingWorkflowState:
        return self.graph.invoke(state)

    def checkpoint(self, state: FamilyFundingWorkflowState, previous: dict | None = None) -> dict:
        prior = previous or {}
        return {
            "engine": self.engine_name,
            "version": self.version,
            "stage": state.get("stage", "FAILED"),
            "checkpoint_seq": int(prior.get("checkpoint_seq", 0)) + 1,
            "trace": list(state.get("trace", [])),
            "resumable": state.get("stage") in {
                "WAITING_INPUT", "WAITING_SELECTION", "PENDING_CONFIRMATION", "WAITING_HUMAN"
            },
            "error_code": state.get("error_code"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _entry_route(state: FamilyFundingWorkflowState) -> str:
        return {
            "CREATE": "create", "SELECT": "select", "CONFIRM": "confirm",
            "HANDOFF": "handoff", "REJECT": "reject", "CANCEL": "cancel", "UNDO": "undo",
        }[state.get("event", "CREATE")]

    @staticmethod
    def _trace(state: FamilyFundingWorkflowState, node: str) -> list[str]:
        return [*state.get("trace", []), node]

    def _load_context(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "CONTEXT_LOADED", "trace": self._trace(state, "load_context")}

    def _complete_constraints(self, state: FamilyFundingWorkflowState) -> dict:
        return {
            "stage": "CONSTRAINTS_INCOMPLETE" if state.get("missing_fields") else "CONSTRAINTS_COMPLETE",
            "trace": self._trace(state, "complete_constraints"),
        }

    def _await_input(self, state: FamilyFundingWorkflowState) -> dict:
        return {
            "stage": "WAITING_INPUT",
            "message": "资金约束不完整，需要补充后才能计算方案。",
            "trace": self._trace(state, "await_input"),
        }

    def _build_timeline(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "TIMELINE_BUILT", "trace": self._trace(state, "build_timeline")}

    def _calculate_plans(self, state: FamilyFundingWorkflowState) -> dict:
        planning = FundingPlanningRequest.model_validate(state["planning"])
        result = self.planner.build_plans(planning)
        return {
            "stage": "PLANS_CALCULATED",
            "plans": [item.model_dump(mode="json") for item in result.plans],
            "trace": self._trace(state, "calculate_plans"),
        }

    def _await_selection(self, state: FamilyFundingWorkflowState) -> dict:
        return {
            "stage": "WAITING_SELECTION",
            "message": "请选择一个方案；系统不会依据家庭身份替你决定。",
            "trace": self._trace(state, "await_selection"),
        }

    def _create_candidate(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "CANDIDATE_CREATED", "trace": self._trace(state, "create_candidate")}

    def _await_confirmation(self, state: FamilyFundingWorkflowState) -> dict:
        return {
            "stage": "PENDING_CONFIRMATION",
            "message": "候选变更已生成；确认前不会修改正式目标、预留资金或发起转账。",
            "trace": self._trace(state, "await_confirmation"),
        }

    def _apply_confirmation(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "APPLIED", "trace": self._trace(state, "apply_confirmation")}

    def _human_handoff(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "WAITING_HUMAN", "trace": self._trace(state, "human_handoff")}

    def _reject(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "REJECTED", "trace": self._trace(state, "reject")}

    def _cancel(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "CANCELED", "trace": self._trace(state, "cancel")}

    def _undo(self, state: FamilyFundingWorkflowState) -> dict:
        return {"stage": "UNDONE", "trace": self._trace(state, "undo")}
