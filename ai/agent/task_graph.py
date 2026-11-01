"""任务图节点的版本化运行契约与状态转换规则。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ai.tools.registry import is_allowed_tool, is_protected_write_tool


TASK_GRAPH_ERROR_MESSAGES = {
    "INVALID_TASK_NODE_TRANSITION": "任务节点状态转换不合法，流程已停止自动推进。",
    "TASK_NODE_STATUS_REASON_REQUIRED": "任务节点缺少等待、失败或跳过原因，流程已停止自动推进。",
    "TASK_NODE_INPUT_DIGEST_REQUIRED": "任务节点缺少输入摘要，不能开始执行。",
    "TASK_NODE_INPUT_TAMPERED": "任务节点输入与已保存摘要不一致，流程已停止自动推进。",
    "TASK_NODE_RECEIPT_REQUIRED": "任务节点缺少可信输出回执，不能声明执行成功。",
    "TASK_NODE_ERROR_CODE_REQUIRED": "任务节点失败但缺少稳定错误码，流程已停止自动推进。",
    "TASK_NODE_ERROR_CODE_NOT_ALLOWED": "非失败节点不能携带错误码，流程已停止自动推进。",
    "TASK_NODE_TIME_INVALID": "任务节点时间证据不合法，流程已停止自动推进。",
    "TASK_NODE_EVIDENCE_INVALID": "任务节点执行证据不完整或不一致，流程已停止自动推进。",
    "TASK_GRAPH_NODE_ID_DUPLICATE": "任务图包含重复节点编号，不能进入执行阶段。",
    "TASK_GRAPH_DEPENDENCY_DUPLICATE": "任务节点包含重复依赖，不能进入执行阶段。",
    "TASK_GRAPH_DEPENDENCY_NOT_FOUND": "任务节点引用了不存在的依赖，不能进入执行阶段。",
    "TASK_GRAPH_SELF_DEPENDENCY": "任务节点不能依赖自身，不能进入执行阶段。",
    "TASK_GRAPH_CYCLE_DETECTED": "任务图存在循环依赖，不能进入执行阶段。",
    "TASK_GRAPH_TOOL_REQUIRED": "工具或策略节点缺少工具名称，不能进入执行阶段。",
    "TASK_GRAPH_TOOL_NOT_ALLOWED": "任务图引用了未授权工具，不能进入执行阶段。",
    "TASK_GRAPH_TOOL_FORBIDDEN_FOR_NODE_TYPE": "当前节点类型不能调用工具，不能进入执行阶段。",
    "TASK_GRAPH_WRITE_POLICY_REQUIRED": "受保护写操作缺少前置策略节点，不能进入执行阶段。",
    "TASK_GRAPH_WRITE_AUTHORIZATION_REQUIRED": "受保护写操作缺少前置人工授权节点，不能进入执行阶段。",
    "TASK_GRAPH_HANDLER_NOT_REGISTERED": "任务节点没有已注册的后端处理器，流程已停止自动推进。",
    "TASK_GRAPH_HANDLER_RESULT_INVALID": "任务节点返回了无效结果，流程已停止自动推进。",
    "TASK_GRAPH_NODE_EXECUTION_FAILED": "任务节点执行失败，系统未声明业务操作成功。",
    "TASK_GRAPH_RECOVERY_AUTHORIZATION_MISSING": "流程恢复缺少可信授权依据，已停止自动推进。",
}


class TaskGraphContractError(ValueError):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = TASK_GRAPH_ERROR_MESSAGES.get(
            code, "任务图证据不符合运行契约，流程已停止自动推进。"
        )
        self.details = details or {}


def canonical_payload_digest(payload: JsonValue) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskNodeStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TaskNodeType(str, Enum):
    VALIDATION = "VALIDATION"
    TOOL = "TOOL"
    POLICY = "POLICY"
    HUMAN_CONFIRM = "HUMAN_CONFIRM"
    RESPONSE = "RESPONSE"


class TaskGraphExecutionMode(str, Enum):
    DECLARATIVE = "DECLARATIVE"
    EXECUTABLE = "EXECUTABLE"


class TaskNodeReceiptKind(str, Enum):
    TOOL_RESULT = "TOOL_RESULT"
    POLICY_DECISION = "POLICY_DECISION"
    USER_DECISION = "USER_DECISION"
    SYSTEM_RESULT = "SYSTEM_RESULT"


class TaskNodeOutputReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TaskNodeReceiptKind
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(default=None, min_length=1, max_length=128)


class TaskNodeRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskNodeStatus = TaskNodeStatus.PENDING
    revision: int = Field(default=0, ge=0)
    reason: str | None = Field(default=None, min_length=1, max_length=200)
    attempt: int = Field(default=0, ge=0)
    input_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_receipt: TaskNodeOutputReceipt | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,63}$")

    @model_validator(mode="after")
    def validate_evidence(self) -> "TaskNodeRuntime":
        for value in (self.started_at, self.finished_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("TASK_NODE_TIME_INVALID")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("TASK_NODE_TIME_INVALID")
        if self.status in REASON_REQUIRED_STATUSES and not self.reason:
            raise ValueError("TASK_NODE_STATUS_REASON_REQUIRED")
        if self.status in {
            TaskNodeStatus.RUNNING,
            TaskNodeStatus.WAITING,
            TaskNodeStatus.SUCCEEDED,
            TaskNodeStatus.FAILED,
        }:
            if self.attempt < 1 or self.started_at is None:
                raise ValueError("TASK_NODE_TIME_INVALID")
        if self.status in {TaskNodeStatus.SUCCEEDED, TaskNodeStatus.FAILED}:
            if self.finished_at is None:
                raise ValueError("TASK_NODE_TIME_INVALID")
        if self.status == TaskNodeStatus.SUCCEEDED and self.output_receipt is None:
            raise ValueError("TASK_NODE_RECEIPT_REQUIRED")
        if self.status == TaskNodeStatus.FAILED and self.error_code is None:
            raise ValueError("TASK_NODE_ERROR_CODE_REQUIRED")
        if self.status != TaskNodeStatus.FAILED and self.error_code is not None:
            raise ValueError("TASK_NODE_ERROR_CODE_NOT_ALLOWED")
        return self


class TaskNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: TaskNodeType
    label: str = Field(min_length=1, max_length=100)
    tool: str | None = Field(default=None, min_length=1, max_length=100)
    input: dict[str, JsonValue] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    runtime: TaskNodeRuntime = Field(default_factory=TaskNodeRuntime)

    @model_validator(mode="after")
    def bind_input_digest(self) -> "TaskNode":
        expected = canonical_payload_digest(self.input)
        if self.runtime.input_digest is None:
            self.runtime = self.runtime.model_copy(update={"input_digest": expected})
        elif self.runtime.input_digest != expected:
            raise ValueError("TASK_NODE_INPUT_TAMPERED")
        return self


class TaskGraphPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    execution_mode: TaskGraphExecutionMode = TaskGraphExecutionMode.DECLARATIVE
    plan_id: str = Field(pattern=r"^plan_[A-Za-z0-9_-]{1,64}$")
    nodes: list[TaskNode] = Field(min_length=1)


def validate_task_graph(plan: TaskGraphPlan | dict[str, Any]) -> TaskGraphPlan:
    validated = plan if isinstance(plan, TaskGraphPlan) else TaskGraphPlan.model_validate(plan)
    nodes_by_id: dict[str, TaskNode] = {}
    for node in validated.nodes:
        if node.id in nodes_by_id:
            raise TaskGraphContractError(
                "TASK_GRAPH_NODE_ID_DUPLICATE", details={"node_id": node.id}
            )
        nodes_by_id[node.id] = node

    for node in validated.nodes:
        if len(node.depends_on) != len(set(node.depends_on)):
            raise TaskGraphContractError(
                "TASK_GRAPH_DEPENDENCY_DUPLICATE", details={"node_id": node.id}
            )
        if node.id in node.depends_on:
            raise TaskGraphContractError(
                "TASK_GRAPH_SELF_DEPENDENCY", details={"node_id": node.id}
            )
        missing = sorted(set(node.depends_on) - nodes_by_id.keys())
        if missing:
            raise TaskGraphContractError(
                "TASK_GRAPH_DEPENDENCY_NOT_FOUND",
                details={"node_id": node.id, "dependency_ids": missing},
            )

        tool_node = node.type in {TaskNodeType.TOOL, TaskNodeType.POLICY}
        if tool_node and not node.tool:
            raise TaskGraphContractError(
                "TASK_GRAPH_TOOL_REQUIRED", details={"node_id": node.id}
            )
        if not tool_node and node.tool:
            raise TaskGraphContractError(
                "TASK_GRAPH_TOOL_FORBIDDEN_FOR_NODE_TYPE",
                details={"node_id": node.id, "node_type": node.type.value},
            )
        if node.tool and not is_allowed_tool(node.tool):
            raise TaskGraphContractError(
                "TASK_GRAPH_TOOL_NOT_ALLOWED",
                details={"node_id": node.id, "tool": node.tool},
            )

    dependency_count = {node.id: len(node.depends_on) for node in validated.nodes}
    dependents: dict[str, list[str]] = {node.id: [] for node in validated.nodes}
    for node in validated.nodes:
        for dependency_id in node.depends_on:
            dependents[dependency_id].append(node.id)
    ready = sorted(node_id for node_id, count in dependency_count.items() if count == 0)
    topological_order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        topological_order.append(node_id)
        for dependent_id in sorted(dependents[node_id]):
            dependency_count[dependent_id] -= 1
            if dependency_count[dependent_id] == 0:
                ready.append(dependent_id)
                ready.sort()
    if len(topological_order) != len(validated.nodes):
        cyclic = sorted(
            node_id for node_id, count in dependency_count.items() if count > 0
        )
        raise TaskGraphContractError(
            "TASK_GRAPH_CYCLE_DETECTED", details={"node_ids": cyclic}
        )

    ancestors: dict[str, set[str]] = {}
    for node_id in topological_order:
        node_ancestors: set[str] = set()
        for dependency_id in nodes_by_id[node_id].depends_on:
            node_ancestors.add(dependency_id)
            node_ancestors.update(ancestors[dependency_id])
        ancestors[node_id] = node_ancestors

    for node in validated.nodes:
        if not node.tool or not is_protected_write_tool(node.tool):
            continue
        ancestor_nodes = [nodes_by_id[node_id] for node_id in ancestors[node.id]]
        if not any(item.type == TaskNodeType.POLICY for item in ancestor_nodes):
            raise TaskGraphContractError(
                "TASK_GRAPH_WRITE_POLICY_REQUIRED",
                details={"node_id": node.id, "tool": node.tool},
            )
        if not any(item.type == TaskNodeType.HUMAN_CONFIRM for item in ancestor_nodes):
            raise TaskGraphContractError(
                "TASK_GRAPH_WRITE_AUTHORIZATION_REQUIRED",
                details={"node_id": node.id, "tool": node.tool},
            )
    return validated


ALLOWED_NODE_TRANSITIONS: dict[TaskNodeStatus, frozenset[TaskNodeStatus]] = {
    TaskNodeStatus.PENDING: frozenset({TaskNodeStatus.READY, TaskNodeStatus.SKIPPED}),
    TaskNodeStatus.READY: frozenset({TaskNodeStatus.RUNNING, TaskNodeStatus.SKIPPED}),
    TaskNodeStatus.RUNNING: frozenset({
        TaskNodeStatus.READY,
        TaskNodeStatus.WAITING,
        TaskNodeStatus.SUCCEEDED,
        TaskNodeStatus.FAILED,
    }),
    TaskNodeStatus.WAITING: frozenset({
        TaskNodeStatus.READY,
        TaskNodeStatus.RUNNING,
        TaskNodeStatus.FAILED,
        TaskNodeStatus.SKIPPED,
    }),
    TaskNodeStatus.SUCCEEDED: frozenset(),
    TaskNodeStatus.FAILED: frozenset(),
    TaskNodeStatus.SKIPPED: frozenset(),
}

REASON_REQUIRED_STATUSES = frozenset({
    TaskNodeStatus.WAITING,
    TaskNodeStatus.FAILED,
    TaskNodeStatus.SKIPPED,
})


TaskNodeTransitionError = TaskGraphContractError


def create_output_receipt(
    *,
    kind: TaskNodeReceiptKind,
    output: JsonValue,
    summary: str,
    reference_id: str | None = None,
) -> TaskNodeOutputReceipt:
    return TaskNodeOutputReceipt(
        kind=kind,
        output_digest=canonical_payload_digest(output),
        summary=summary,
        reference_id=reference_id,
    )


def transition_node_runtime(
    runtime: TaskNodeRuntime,
    target: TaskNodeStatus,
    *,
    reason: str | None = None,
    output_receipt: TaskNodeOutputReceipt | None = None,
    error_code: str | None = None,
    at: datetime | None = None,
) -> TaskNodeRuntime:
    if target not in ALLOWED_NODE_TRANSITIONS[runtime.status]:
        raise TaskGraphContractError(
            "INVALID_TASK_NODE_TRANSITION",
            details={"from": runtime.status.value, "to": target.value},
        )
    normalized_reason = reason.strip() if reason else None
    if target in REASON_REQUIRED_STATUSES and not normalized_reason:
        raise TaskGraphContractError("TASK_NODE_STATUS_REASON_REQUIRED")
    if target in {TaskNodeStatus.READY, TaskNodeStatus.RUNNING} and not runtime.input_digest:
        raise TaskGraphContractError("TASK_NODE_INPUT_DIGEST_REQUIRED")

    event_time = at or datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        raise TaskGraphContractError("TASK_NODE_TIME_INVALID")

    updates: dict[str, Any] = {
        "status": target,
        "revision": runtime.revision + 1,
        "reason": normalized_reason,
        "output_receipt": None,
        "error_code": None,
    }
    if target == TaskNodeStatus.RUNNING:
        updates["attempt"] = runtime.attempt + 1
        updates["started_at"] = event_time
    elif target == TaskNodeStatus.SUCCEEDED:
        if output_receipt is None:
            raise TaskGraphContractError("TASK_NODE_RECEIPT_REQUIRED")
        updates["output_receipt"] = output_receipt
        updates["finished_at"] = event_time
    elif target == TaskNodeStatus.FAILED:
        if not error_code:
            raise TaskGraphContractError("TASK_NODE_ERROR_CODE_REQUIRED")
        updates["error_code"] = error_code
        updates["finished_at"] = event_time
    elif target == TaskNodeStatus.SKIPPED:
        updates["finished_at"] = event_time

    try:
        return TaskNodeRuntime.model_validate({
            **runtime.model_dump(),
            **updates,
        })
    except ValueError as error:
        code = next(
            (
                candidate
                for candidate in TASK_GRAPH_ERROR_MESSAGES
                if candidate in str(error)
            ),
            "TASK_NODE_EVIDENCE_INVALID",
        )
        raise TaskGraphContractError(code) from error
