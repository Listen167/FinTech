from copy import deepcopy

import pytest

from ai.agent.task_graph import (
    TaskGraphContractError,
    TaskGraphPlan,
    TaskNodeReceiptKind,
    TaskNodeStatus,
)
from backend.app.services.task_graph_executor import (
    TaskExecutionContext,
    TaskGraphExecutor,
    TaskNodeOutcome,
)


def plan_with(nodes: list[dict]) -> dict:
    return TaskGraphPlan(plan_id="plan_executor_test", nodes=nodes).model_dump(
        mode="json", exclude_none=True
    )


def test_executor_runs_only_registered_handlers_in_dependency_order() -> None:
    calls: list[str] = []

    def read_handler(node, _context):
        calls.append(node.id)
        return TaskNodeOutcome.succeeded(
            summary="只读工具执行完成",
            output={"node_id": node.id},
            receipt_kind=TaskNodeReceiptKind.TOOL_RESULT,
        )

    executor = TaskGraphExecutor({"transaction.list": read_handler})
    run = {"run_id": "run_test"}
    result = executor.execute(
        plan_with([
            {
                "id": "n1",
                "type": "TOOL",
                "label": "读取一",
                "tool": "transaction.list",
            },
            {
                "id": "n2",
                "type": "TOOL",
                "label": "读取二",
                "tool": "transaction.list",
                "depends_on": ["n1"],
            },
        ]),
        TaskExecutionContext(run=run),
    )

    assert calls == ["n1", "n2"]
    assert result["execution_mode"] == "EXECUTABLE"
    assert all(node["runtime"]["status"] == "SUCCEEDED" for node in result["nodes"])
    assert all(node["runtime"]["output_receipt"] for node in result["nodes"])


def test_unknown_backend_handler_fails_closed_and_stops_dependents() -> None:
    executor = TaskGraphExecutor({})
    result = executor.execute(
        plan_with([
            {
                "id": "n1",
                "type": "TOOL",
                "label": "余额",
                "tool": "account.balance.get",
            },
            {
                "id": "n2",
                "type": "RESPONSE",
                "label": "响应",
                "depends_on": ["n1"],
            },
        ]),
        TaskExecutionContext(run={"run_id": "run_test"}),
    )

    assert result["nodes"][0]["runtime"]["status"] == "FAILED"
    assert result["nodes"][0]["runtime"]["error_code"] == "TASK_GRAPH_HANDLER_NOT_REGISTERED"
    assert result["nodes"][1]["runtime"]["status"] == "PENDING"


def test_waiting_node_resumes_only_with_explicit_node_id() -> None:
    def confirm_handler(_node, context):
        if not context.authorization:
            return TaskNodeOutcome.waiting(reason="USER_CONFIRMATION_REQUIRED")
        return TaskNodeOutcome.succeeded(
            summary="用户已确认",
            output={"decision": "APPROVED"},
            receipt_kind=TaskNodeReceiptKind.USER_DECISION,
        )

    executor = TaskGraphExecutor({"node:HUMAN_CONFIRM": confirm_handler})
    run = {"run_id": "run_test"}
    waiting = executor.execute(
        plan_with([
            {"id": "n1", "type": "HUMAN_CONFIRM", "label": "确认"},
        ]),
        TaskExecutionContext(run=run),
    )
    resumed = executor.execute(
        waiting,
        TaskExecutionContext(run=run, authorization={"approved": True}),
        resume_node_id="n1",
    )

    assert waiting["nodes"][0]["runtime"]["status"] == "WAITING"
    assert resumed["nodes"][0]["runtime"]["status"] == "SUCCEEDED"
    assert resumed["nodes"][0]["runtime"]["attempt"] == 2


def test_handler_cannot_claim_success_without_receipt_output() -> None:
    def invalid_handler(_node, _context):
        return TaskNodeOutcome(status=TaskNodeStatus.SUCCEEDED, summary="伪成功")

    executor = TaskGraphExecutor({"transaction.list": invalid_handler})
    result = executor.execute(
        plan_with([
            {
                "id": "n1",
                "type": "TOOL",
                "label": "读取",
                "tool": "transaction.list",
            }
        ]),
        TaskExecutionContext(run={"run_id": "run_test"}),
    )

    assert result["nodes"][0]["runtime"]["status"] == "FAILED"
    assert result["nodes"][0]["runtime"]["error_code"] == "TASK_NODE_RECEIPT_REQUIRED"


def test_executor_revalidates_input_digest_before_resume() -> None:
    def wait_handler(_node, _context):
        return TaskNodeOutcome.waiting(reason="USER_CONFIRMATION_REQUIRED")

    executor = TaskGraphExecutor({"node:HUMAN_CONFIRM": wait_handler})
    waiting = executor.execute(
        plan_with([
            {
                "id": "n1",
                "type": "HUMAN_CONFIRM",
                "label": "确认",
                "input": {"amount": 200},
            }
        ]),
        TaskExecutionContext(run={"run_id": "run_test"}),
    )
    tampered = deepcopy(waiting)
    tampered["nodes"][0]["input"]["amount"] = 2000

    with pytest.raises(TaskGraphContractError) as captured:
        executor.execute(
            tampered,
            TaskExecutionContext(run={"run_id": "run_test"}),
            resume_node_id="n1",
        )

    assert captured.value.code == "TASK_NODE_INPUT_TAMPERED"


def test_skip_unfinished_marks_waiting_and_downstream_nodes() -> None:
    def wait_handler(_node, _context):
        return TaskNodeOutcome.waiting(reason="USER_CONFIRMATION_REQUIRED")

    executor = TaskGraphExecutor({"node:HUMAN_CONFIRM": wait_handler})
    context = TaskExecutionContext(run={"run_id": "run_test"})
    waiting = executor.execute(
        plan_with([
            {"id": "n1", "type": "HUMAN_CONFIRM", "label": "确认"},
            {"id": "n2", "type": "RESPONSE", "label": "响应", "depends_on": ["n1"]},
        ]),
        context,
    )
    skipped = executor.skip_unfinished(waiting, context, reason="USER_ABORTED")

    assert [node["runtime"]["status"] for node in skipped["nodes"]] == [
        "SKIPPED",
        "SKIPPED",
    ]
    assert all(node["runtime"]["reason"] == "USER_ABORTED" for node in skipped["nodes"])
