"""确定性任务图执行器。

执行器只分派构造时显式注册的处理器，不解析或执行模型生成的代码。
业务处理器负责调用服务层，执行器负责依赖、状态、证据和停止条件。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import JsonValue, ValidationError

from ai.agent.task_graph import (
    TASK_GRAPH_ERROR_MESSAGES,
    TaskGraphContractError,
    TaskGraphExecutionMode,
    TaskGraphPlan,
    TaskNode,
    TaskNodeReceiptKind,
    TaskNodeStatus,
    create_output_receipt,
    transition_node_runtime,
    validate_task_graph,
)


logger = logging.getLogger(__name__)


TASK_GRAPH_HANDLER_ERROR_MESSAGES = {
    "TASK_GRAPH_HANDLER_NOT_REGISTERED": "任务节点没有已注册的后端处理器，流程已停止自动推进。",
    "TASK_GRAPH_HANDLER_RESULT_INVALID": "任务节点返回了无效结果，流程已停止自动推进。",
    "TASK_GRAPH_NODE_EXECUTION_FAILED": "任务节点执行失败，系统未声明业务操作成功。",
    "TASK_GRAPH_RECOVERY_AUTHORIZATION_MISSING": "流程恢复缺少可信授权依据，已停止自动推进。",
}
EXECUTOR_VERSION = "deterministic-executor-2.0.0"


class TaskGraphExecutionError(TaskGraphContractError):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, details=details)
        self.message = TASK_GRAPH_HANDLER_ERROR_MESSAGES.get(code, self.message)


_OUTPUT_UNSET = object()


@dataclass(frozen=True)
class TaskNodeOutcome:
    status: TaskNodeStatus
    summary: str = ""
    output: JsonValue | object = _OUTPUT_UNSET
    receipt_kind: TaskNodeReceiptKind = TaskNodeReceiptKind.SYSTEM_RESULT
    reference_id: str | None = None
    reason: str | None = None
    error_code: str | None = None

    @classmethod
    def succeeded(
        cls,
        *,
        summary: str,
        output: JsonValue,
        receipt_kind: TaskNodeReceiptKind = TaskNodeReceiptKind.SYSTEM_RESULT,
        reference_id: str | None = None,
    ) -> "TaskNodeOutcome":
        return cls(
            status=TaskNodeStatus.SUCCEEDED,
            summary=summary,
            output=output,
            receipt_kind=receipt_kind,
            reference_id=reference_id,
        )

    @classmethod
    def waiting(cls, *, reason: str) -> "TaskNodeOutcome":
        return cls(status=TaskNodeStatus.WAITING, reason=reason)

    @classmethod
    def failed(cls, *, error_code: str, reason: str) -> "TaskNodeOutcome":
        return cls(
            status=TaskNodeStatus.FAILED,
            error_code=error_code,
            reason=reason,
        )


@dataclass
class TaskExecutionContext:
    run: dict[str, Any]
    authorization: dict[str, Any] | None = None
    recovery: bool = False
    values: dict[str, Any] = field(default_factory=dict)


TaskNodeHandler = Callable[[TaskNode, TaskExecutionContext], TaskNodeOutcome]
PersistCallback = Callable[[dict[str, Any]], None]


class TaskGraphExecutor:
    """按计划顺序确定性推进就绪节点，并在等待或失败时立即停止。"""

    def __init__(
        self,
        handlers: dict[str, TaskNodeHandler],
        *,
        persist: PersistCallback | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._persist = persist

    def execute(
        self,
        plan: TaskGraphPlan | dict[str, Any],
        context: TaskExecutionContext,
        *,
        resume_node_id: str | None = None,
        recover_interrupted: bool = False,
    ) -> dict[str, Any]:
        validated = self._validate(plan)
        validated.execution_mode = TaskGraphExecutionMode.EXECUTABLE

        if recover_interrupted:
            for node in validated.nodes:
                if node.runtime.status == TaskNodeStatus.RUNNING:
                    node.runtime = transition_node_runtime(
                        node.runtime,
                        TaskNodeStatus.READY,
                    )
                    self._checkpoint(validated, context)

        if resume_node_id is not None:
            node = self._node(validated, resume_node_id)
            if node.runtime.status != TaskNodeStatus.WAITING:
                raise TaskGraphExecutionError(
                    "INVALID_TASK_NODE_TRANSITION",
                    details={
                        "node_id": resume_node_id,
                        "from": node.runtime.status.value,
                        "to": TaskNodeStatus.READY.value,
                    },
                )
            node.runtime = transition_node_runtime(node.runtime, TaskNodeStatus.READY)
            self._checkpoint(validated, context)

        while True:
            progressed = False
            nodes_by_id = {node.id: node for node in validated.nodes}
            for node in validated.nodes:
                if node.runtime.status == TaskNodeStatus.WAITING:
                    return self._dump(validated)
                if node.runtime.status == TaskNodeStatus.FAILED:
                    return self._dump(validated)
                if node.runtime.status != TaskNodeStatus.PENDING:
                    continue

                dependency_statuses = [
                    nodes_by_id[dependency_id].runtime.status
                    for dependency_id in node.depends_on
                ]
                if any(
                    status in {TaskNodeStatus.FAILED, TaskNodeStatus.SKIPPED}
                    for status in dependency_statuses
                ):
                    node.runtime = transition_node_runtime(
                        node.runtime,
                        TaskNodeStatus.SKIPPED,
                        reason="DEPENDENCY_NOT_SUCCEEDED",
                    )
                    self._checkpoint(validated, context)
                    progressed = True
                    continue
                if not all(status == TaskNodeStatus.SUCCEEDED for status in dependency_statuses):
                    continue
                node.runtime = transition_node_runtime(node.runtime, TaskNodeStatus.READY)
                self._checkpoint(validated, context)
                progressed = True

            ready = next(
                (
                    node
                    for node in validated.nodes
                    if node.runtime.status == TaskNodeStatus.READY
                ),
                None,
            )
            if ready is not None:
                self._run_node(validated, ready, context)
                progressed = True
                if ready.runtime.status in {TaskNodeStatus.WAITING, TaskNodeStatus.FAILED}:
                    return self._dump(validated)
                continue

            if not progressed:
                return self._dump(validated)

    def skip_unfinished(
        self,
        plan: TaskGraphPlan | dict[str, Any],
        context: TaskExecutionContext,
        *,
        reason: str,
    ) -> dict[str, Any]:
        validated = self._validate(plan)
        validated.execution_mode = TaskGraphExecutionMode.EXECUTABLE
        for node in validated.nodes:
            if node.runtime.status in {
                TaskNodeStatus.PENDING,
                TaskNodeStatus.READY,
                TaskNodeStatus.WAITING,
            }:
                node.runtime = transition_node_runtime(
                    node.runtime,
                    TaskNodeStatus.SKIPPED,
                    reason=reason,
                )
                self._checkpoint(validated, context)
        return self._dump(validated)

    def _run_node(
        self,
        plan: TaskGraphPlan,
        node: TaskNode,
        context: TaskExecutionContext,
    ) -> None:
        node.runtime = transition_node_runtime(node.runtime, TaskNodeStatus.RUNNING)
        self._checkpoint(plan, context)
        handler_key = node.tool or f"node:{node.type.value}"
        handler = self._handlers.get(handler_key)
        if handler is None:
            outcome = TaskNodeOutcome.failed(
                error_code="TASK_GRAPH_HANDLER_NOT_REGISTERED",
                reason="TASK_GRAPH_HANDLER_NOT_REGISTERED",
            )
        else:
            try:
                outcome = handler(node, context)
                self._validate_outcome(outcome)
            except TaskGraphContractError as error:
                outcome = TaskNodeOutcome.failed(
                    error_code=error.code,
                    reason=error.code,
                )
            except Exception as error:
                logger.exception(
                    "任务图节点处理器异常 handler=%s node_id=%s exception_type=%s",
                    handler_key,
                    node.id,
                    type(error).__name__,
                )
                outcome = TaskNodeOutcome.failed(
                    error_code="TASK_GRAPH_NODE_EXECUTION_FAILED",
                    reason="TASK_GRAPH_NODE_EXECUTION_FAILED",
                )

        if outcome.status == TaskNodeStatus.SUCCEEDED:
            if outcome.output is _OUTPUT_UNSET:
                outcome = TaskNodeOutcome.failed(
                    error_code="TASK_NODE_RECEIPT_REQUIRED",
                    reason="TASK_NODE_RECEIPT_REQUIRED",
                )
            else:
                receipt = create_output_receipt(
                    kind=outcome.receipt_kind,
                    output=outcome.output,
                    summary=outcome.summary,
                    reference_id=outcome.reference_id,
                )
                node.runtime = transition_node_runtime(
                    node.runtime,
                    TaskNodeStatus.SUCCEEDED,
                    output_receipt=receipt,
                )
                self._checkpoint(plan, context)
                return

        node.runtime = transition_node_runtime(
            node.runtime,
            outcome.status,
            reason=outcome.reason,
            error_code=outcome.error_code,
        )
        self._checkpoint(plan, context)

    @staticmethod
    def _validate_outcome(outcome: TaskNodeOutcome) -> None:
        if not isinstance(outcome, TaskNodeOutcome):
            raise TaskGraphExecutionError("TASK_GRAPH_HANDLER_RESULT_INVALID")
        if outcome.status not in {
            TaskNodeStatus.SUCCEEDED,
            TaskNodeStatus.WAITING,
            TaskNodeStatus.FAILED,
        }:
            raise TaskGraphExecutionError("TASK_GRAPH_HANDLER_RESULT_INVALID")
        if outcome.status == TaskNodeStatus.SUCCEEDED and not outcome.summary:
            raise TaskGraphExecutionError("TASK_NODE_RECEIPT_REQUIRED")

    @staticmethod
    def _node(plan: TaskGraphPlan, node_id: str) -> TaskNode:
        node = next((item for item in plan.nodes if item.id == node_id), None)
        if node is None:
            raise TaskGraphExecutionError(
                "TASK_GRAPH_DEPENDENCY_NOT_FOUND",
                details={"node_id": node_id},
            )
        return node

    @staticmethod
    def _validate(plan: TaskGraphPlan | dict[str, Any]) -> TaskGraphPlan:
        try:
            candidate = deepcopy(plan)
            return validate_task_graph(candidate)
        except TaskGraphContractError:
            raise
        except ValidationError as error:
            text = str(error)
            code = next(
                (item for item in TASK_GRAPH_ERROR_MESSAGES if item in text),
                "TASK_NODE_EVIDENCE_INVALID",
            )
            raise TaskGraphExecutionError(code) from error

    def _checkpoint(self, plan: TaskGraphPlan, context: TaskExecutionContext) -> None:
        payload = self._dump(plan)
        context.run["plan"] = payload
        if self._persist is not None:
            self._persist(payload)

    @staticmethod
    def _dump(plan: TaskGraphPlan) -> dict[str, Any]:
        return plan.model_dump(mode="json", exclude_none=True)
