"""使用 LangGraph 编排生活事件补槽、候选生成、确认和恢复。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.services.life_goal_conflict_service import (
    LifeGoalConflictError,
    LifeGoalConflictService,
)
from backend.app.services.life_graph_proposal import (
    LifeGraphProposalError,
    build_life_graph_proposal,
)
from backend.app.services.life_graph_service import LifeGraphService, LifeGraphServiceError


WorkflowEvent = Literal[
    "MESSAGE",
    "VALIDATE",
    "CREATE_CANDIDATE",
    "ANALYZE_CONFLICTS",
    "AWAIT_CONFIRMATION",
    "DECISION",
    "RECOVER",
    "HANDOFF",
    "ABORT",
]


class LifeEventWorkflowState(TypedDict, total=False):
    event: WorkflowEvent
    run_id: str
    user_id: str
    session_id: str
    source_run_id: str | None
    intent: dict
    previous_workflow: dict
    decision_status: str
    handoff_reason: str
    stage: str
    run_state: str
    message: str
    missing_slots: list[str]
    change_request: dict
    conflict_analysis: dict | None
    error_code: str | None
    trace: list[str]
    recovered: bool


class LifeEventWorkflow:
    """确定性 LangGraph；所有业务写入仍通过现有服务层完成。"""

    engine_name = "langgraph"
    version = 1

    def __init__(
        self,
        life_graph: LifeGraphService | None,
        goal_conflicts: LifeGoalConflictService | None,
    ) -> None:
        self.life_graph = life_graph
        self.goal_conflicts = goal_conflicts
        builder = StateGraph(LifeEventWorkflowState)
        builder.add_node("validate_slots", self._validate_slots)
        builder.add_node("await_input", self._await_input)
        builder.add_node("create_candidate", self._create_candidate)
        builder.add_node("candidate_failed", self._candidate_failed)
        builder.add_node("analyze_conflicts", self._analyze_conflicts)
        builder.add_node("await_confirmation", self._await_confirmation)
        builder.add_node("apply_decision", self._apply_decision)
        builder.add_node("recover", self._recover)
        builder.add_node("human_handoff", self._human_handoff)
        builder.add_node("abort", self._abort)
        builder.add_conditional_edges(
            START,
            self._entry_route,
            {
                "message": "validate_slots",
                "validate": "validate_slots",
                "create_candidate": "create_candidate",
                "analyze_conflicts": "analyze_conflicts",
                "await_confirmation": "await_confirmation",
                "decision": "apply_decision",
                "recover": "recover",
                "handoff": "human_handoff",
                "abort": "abort",
            },
        )
        builder.add_conditional_edges(
            "validate_slots",
            self._slot_route,
            {
                "missing": "await_input",
                "complete": "create_candidate",
                "validated": END,
            },
        )
        builder.add_conditional_edges(
            "create_candidate",
            self._candidate_route,
            {
                "failed": "candidate_failed",
                "created": "analyze_conflicts",
                "created_only": END,
            },
        )
        builder.add_conditional_edges(
            "analyze_conflicts",
            self._conflict_route,
            {"analyzed": END, "await_confirmation": "await_confirmation"},
        )
        for terminal_node in (
            "await_input",
            "candidate_failed",
            "await_confirmation",
            "apply_decision",
            "recover",
            "human_handoff",
            "abort",
        ):
            builder.add_edge(terminal_node, END)
        self.graph = builder.compile()

    def invoke(self, state: LifeEventWorkflowState) -> LifeEventWorkflowState:
        return self.graph.invoke(state)

    def checkpoint(
        self,
        state: LifeEventWorkflowState,
        previous: dict | None = None,
    ) -> dict:
        prior = previous or {}
        request = state.get("change_request") or {}
        conflict = state.get("conflict_analysis") or {}
        stage = state.get("stage", "FAILED")
        return {
            "engine": self.engine_name,
            "version": self.version,
            "stage": stage,
            "checkpoint_seq": int(prior.get("checkpoint_seq", 0)) + 1,
            "trace": list(state.get("trace", [])),
            "resumable": stage in {
                "WAITING_INPUT",
                "WAITING_CONFIRMATION",
                "WAITING_HUMAN",
            },
            "recovered": bool(state.get("recovered", False)),
            "missing_slots": list(state.get("missing_slots", [])),
            "change_request_id": request.get("request_id")
            or prior.get("change_request_id"),
            "conflict_status": conflict.get("status")
            or prior.get("conflict_status"),
            "error_code": state.get("error_code"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _append_trace(state: LifeEventWorkflowState, node: str) -> list[str]:
        return [*state.get("trace", []), node]

    @staticmethod
    def _entry_route(state: LifeEventWorkflowState) -> str:
        return {
            "MESSAGE": "message",
            "VALIDATE": "validate",
            "CREATE_CANDIDATE": "create_candidate",
            "ANALYZE_CONFLICTS": "analyze_conflicts",
            "AWAIT_CONFIRMATION": "await_confirmation",
            "DECISION": "decision",
            "RECOVER": "recover",
            "HANDOFF": "handoff",
            "ABORT": "abort",
        }.get(state.get("event", "MESSAGE"), "message")

    @staticmethod
    def _slot_route(state: LifeEventWorkflowState) -> str:
        if state.get("event") == "VALIDATE" and not state.get("missing_slots"):
            return "validated"
        return "missing" if state.get("missing_slots") else "complete"

    @staticmethod
    def _candidate_route(state: LifeEventWorkflowState) -> str:
        if state.get("event") == "CREATE_CANDIDATE" and not state.get("error_code"):
            return "created_only"
        return "failed" if state.get("error_code") else "created"

    @staticmethod
    def _conflict_route(state: LifeEventWorkflowState) -> str:
        if state.get("event") == "ANALYZE_CONFLICTS":
            return "analyzed"
        return "await_confirmation"

    def _validate_slots(self, state: LifeEventWorkflowState) -> dict:
        intent = state.get("intent") or {}
        slots = intent.get("slots") or {}
        missing = [
            field
            for field in (
                "related_person",
                "relation_type",
                "event_type",
                "event_date",
            )
            if not slots.get(field)
        ]
        if slots.get("goal_required") and slots.get("target_amount") is None:
            missing.append("target_amount")
        return {
            "stage": "SLOTS_VALIDATED" if not missing else "SLOTS_INCOMPLETE",
            "missing_slots": missing,
            "error_code": None,
            "trace": self._append_trace(state, "validate_slots"),
        }

    def _await_input(self, state: LifeEventWorkflowState) -> dict:
        labels = {
            "related_person": "关系对象",
            "relation_type": "关系类型",
            "event_type": "事件类型",
            "event_date": "具体日期",
            "target_amount": "目标金额",
        }
        missing = "、".join(labels.get(item, item) for item in state["missing_slots"])
        return {
            "stage": "WAITING_INPUT",
            "run_state": "WAITING_INPUT",
            "message": f"还需要补充{missing}。在补充完成前，不会执行任何资金操作。",
            "trace": self._append_trace(state, "await_input"),
        }

    def _create_candidate(self, state: LifeEventWorkflowState) -> dict:
        if self.life_graph is None:
            return {
                "stage": "CANDIDATE_FAILED",
                "error_code": "LIFE_GRAPH_NOT_CONFIGURED",
                "trace": self._append_trace(state, "create_candidate"),
            }
        try:
            change_set, summary = build_life_graph_proposal(
                self.life_graph.repository,
                owner_user_id=state["user_id"],
                slots=state["intent"]["slots"],
            )
            result = self.life_graph.create_change_request(
                owner_user_id=state["user_id"],
                idempotency_key=f"agent-life:{state['run_id']}",
                summary=summary,
                change_set=change_set,
                source_run_id=state.get("source_run_id"),
            )
        except (LifeGraphProposalError, LifeGraphServiceError) as error:
            return {
                "stage": "CANDIDATE_FAILED",
                "error_code": getattr(error, "code", str(error)),
                "trace": self._append_trace(state, "create_candidate"),
            }
        return {
            "stage": "CANDIDATE_CREATED",
            "change_request": result["request"],
            "error_code": None,
            "trace": self._append_trace(state, "create_candidate"),
        }

    def _candidate_failed(self, state: LifeEventWorkflowState) -> dict:
        return {
            "stage": "FAILED",
            "run_state": "FAILED",
            "message": "生活事件候选计划生成失败，未写入任何正式图谱数据。",
            "trace": self._append_trace(state, "candidate_failed"),
        }

    def _analyze_conflicts(self, state: LifeEventWorkflowState) -> dict:
        request = state["change_request"]
        if self.goal_conflicts is None:
            conflict = self._unknown_conflict("LIFE_GOAL_CONFLICT_SERVICE_NOT_CONFIGURED")
        else:
            try:
                conflict = self.goal_conflicts.analyze(
                    owner_user_id=state["user_id"],
                    candidate_request_id=request["request_id"],
                )
            except LifeGoalConflictError as error:
                conflict = self._unknown_conflict(error.code)
        return {
            "stage": "CONFLICT_ANALYZED",
            "conflict_analysis": conflict,
            "trace": self._append_trace(state, "analyze_conflicts"),
        }

    def _await_confirmation(self, state: LifeEventWorkflowState) -> dict:
        conflict = state.get("conflict_analysis") or {}
        status = conflict.get("status")
        message = "已生成生活关系、事件和资金目标候选方案，请核对后确认。尚未预留或划转任何资金。"
        if status == "CONFLICT":
            message = "候选生活目标存在明确资金缺口，请先查看计算依据并选择调整方案。系统不会替你决定家庭目标优先级。"
        elif status == "PRESSURE":
            message = "候选生活目标可能造成同月现金流压力，请核对计算依据后决定是否调整。尚未预留或划转资金。"
        return {
            "stage": "WAITING_CONFIRMATION",
            "run_state": "WAITING_CONFIRMATION",
            "message": message,
            "trace": self._append_trace(state, "await_confirmation"),
        }

    def _apply_decision(self, state: LifeEventWorkflowState) -> dict:
        status = state.get("decision_status", "")
        trace = self._append_trace(state, "apply_decision")
        if status == "APPROVED":
            return {
                "stage": "COMPLETED",
                "run_state": "SUCCEEDED",
                "message": "生活关系、事件和资金目标已按确认内容写入。资金仍未自动划转。",
                "trace": trace,
            }
        if status == "REJECTED":
            return {
                "stage": "ROLLED_BACK",
                "run_state": "ABORTED",
                "message": "生活图谱候选方案已拒绝，未写入任何正式数据。",
                "trace": trace,
            }
        return {
            "stage": "ABORTED",
            "run_state": "ABORTED",
            "message": "生活图谱候选方案已失效，未写入任何正式数据。",
            "error_code": f"CHANGE_REQUEST_{status or 'INVALID'}",
            "trace": trace,
        }

    def _recover(self, state: LifeEventWorkflowState) -> dict:
        previous = state.get("previous_workflow") or {}
        stage = previous.get("stage", "FAILED")
        trace = [*previous.get("trace", []), "recover"]
        if stage == "WAITING_INPUT":
            return {
                "stage": stage,
                "run_state": "WAITING_INPUT",
                "message": state.get("message", "请继续补充缺失信息。"),
                "missing_slots": list(previous.get("missing_slots", [])),
                "trace": trace,
                "recovered": True,
            }
        if stage == "WAITING_CONFIRMATION":
            return self._recover_confirmation(state, previous, trace)
        if stage == "WAITING_HUMAN":
            return {
                "stage": stage,
                "run_state": "WAITING_HUMAN",
                "message": state.get("message", "任务正在等待人工处理。"),
                "trace": trace,
                "recovered": True,
            }
        return {
            "stage": stage,
            "run_state": state.get("run_state", "FAILED"),
            "message": state.get("message", "流程状态已恢复。"),
            "trace": trace,
            "recovered": True,
        }

    def _recover_confirmation(
        self,
        state: LifeEventWorkflowState,
        previous: dict,
        trace: list[str],
    ) -> dict:
        request_id = previous.get("change_request_id")
        if self.life_graph is None or not request_id:
            return {
                "stage": "WAITING_HUMAN",
                "run_state": "WAITING_HUMAN",
                "message": "自动恢复缺少候选请求依据，已停止自动推进并等待人工处理。",
                "error_code": "WORKFLOW_RECOVERY_EVIDENCE_MISSING",
                "trace": trace,
                "recovered": True,
            }
        try:
            request = self.life_graph.get_change_request(state["user_id"], request_id)
        except LifeGraphServiceError as error:
            return {
                "stage": "WAITING_HUMAN",
                "run_state": "WAITING_HUMAN",
                "message": "候选请求恢复失败，已停止自动推进并等待人工处理。",
                "error_code": error.code,
                "trace": trace,
                "recovered": True,
            }
        if request["status"] == "PENDING":
            return {
                "stage": "WAITING_CONFIRMATION",
                "run_state": "WAITING_CONFIRMATION",
                "message": state.get("message", "候选方案仍在等待确认。"),
                "change_request": request,
                "trace": trace,
                "recovered": True,
            }
        return {
            **self._apply_decision({
                **state,
                "decision_status": request["status"],
                "trace": trace,
            }),
            "change_request": request,
            "recovered": True,
        }

    def _human_handoff(self, state: LifeEventWorkflowState) -> dict:
        return {
            "stage": "WAITING_HUMAN",
            "run_state": "WAITING_HUMAN",
            "message": "自动流程已冻结，任务已转入人工处理队列，不会继续执行资金或图谱写入。",
            "trace": self._append_trace(state, "human_handoff"),
        }

    def _abort(self, state: LifeEventWorkflowState) -> dict:
        return {
            "stage": "ABORTED",
            "run_state": "ABORTED",
            "message": "任务已中断，所有未完成授权均已撤销。",
            "trace": self._append_trace(state, "abort"),
        }

    @staticmethod
    def _unknown_conflict(code: str) -> dict:
        return {
            "status": "UNKNOWN",
            "severity": "UNKNOWN",
            "explanation": f"冲突检测暂不可用：{code}。",
            "priority_decision": "USER_REQUIRED",
            "suggestions": [],
        }
