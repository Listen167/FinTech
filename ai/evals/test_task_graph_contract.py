from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai.agent.intent import parse_intent
from ai.agent.planner import build_plan
from ai.agent.task_graph import (
    TaskGraphPlan,
    TaskNode,
    TaskNodeReceiptKind,
    TaskNodeRuntime,
    TaskNodeStatus,
    TaskNodeTransitionError,
    canonical_payload_digest,
    create_output_receipt,
    transition_node_runtime,
    validate_task_graph,
)


@pytest.mark.parametrize(
    "message",
    [
        "帮我分析本月账单",
        "列出我的订阅",
        "取消云盘会员",
        "给小王转200元",
        "帮我分析本月账单和订阅",
        "我爱人11月18日生日，帮我留1000元买礼物",
        "你好",
    ],
)
def test_every_planner_branch_emits_the_versioned_runtime_contract(message):
    plan = build_plan(parse_intent(message))
    validated = TaskGraphPlan.model_validate(plan)

    assert validated.schema_version == 1
    assert validated.execution_mode.value == "DECLARATIVE"
    assert validated.nodes
    assert all(node.runtime.status == TaskNodeStatus.PENDING for node in validated.nodes)
    assert all(node.runtime.revision == 0 for node in validated.nodes)
    assert all(node.runtime.input_digest == canonical_payload_digest(node.input) for node in validated.nodes)
    assert all(node.runtime.attempt == 0 for node in validated.nodes)


def test_node_runtime_follows_explicit_state_transitions():
    started_at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    pending = TaskNodeRuntime(input_digest=canonical_payload_digest({"amount": 200}))
    ready = transition_node_runtime(pending, TaskNodeStatus.READY, at=started_at)
    running = transition_node_runtime(ready, TaskNodeStatus.RUNNING, at=started_at)
    waiting = transition_node_runtime(
        running,
        TaskNodeStatus.WAITING,
        reason="USER_CONFIRMATION_REQUIRED",
        at=started_at + timedelta(seconds=1),
    )
    resumed = transition_node_runtime(
        waiting,
        TaskNodeStatus.READY,
        at=started_at + timedelta(seconds=2),
    )

    assert [pending.revision, ready.revision, running.revision, waiting.revision, resumed.revision] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert running.attempt == 1
    assert running.started_at == started_at
    assert waiting.reason == "USER_CONFIRMATION_REQUIRED"
    assert resumed.reason is None
    assert resumed.started_at == started_at


def test_waiting_failed_and_skipped_require_a_reason():
    started_at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    running = TaskNodeRuntime(
        status=TaskNodeStatus.RUNNING,
        revision=2,
        attempt=1,
        input_digest=canonical_payload_digest({}),
        started_at=started_at,
    )

    with pytest.raises(TaskNodeTransitionError) as captured:
        transition_node_runtime(running, TaskNodeStatus.FAILED)
    assert captured.value.code == "TASK_NODE_STATUS_REASON_REQUIRED"
    assert "缺少等待、失败或跳过原因" in captured.value.message


def test_terminal_and_out_of_order_transitions_are_rejected():
    with pytest.raises(TaskNodeTransitionError) as pending_error:
        transition_node_runtime(TaskNodeRuntime(), TaskNodeStatus.SUCCEEDED)
    assert pending_error.value.code == "INVALID_TASK_NODE_TRANSITION"
    assert pending_error.value.details == {"from": "PENDING", "to": "SUCCEEDED"}

    receipt = create_output_receipt(
        kind=TaskNodeReceiptKind.SYSTEM_RESULT,
        output={"ok": True},
        summary="处理完成",
    )
    with pytest.raises(TaskNodeTransitionError) as terminal_error:
        transition_node_runtime(
            TaskNodeRuntime(
                status=TaskNodeStatus.SUCCEEDED,
                revision=3,
                attempt=1,
                input_digest=canonical_payload_digest({}),
                output_receipt=receipt,
                started_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 11, 1, 9, 1, tzinfo=timezone.utc),
            ),
            TaskNodeStatus.READY,
        )
    assert terminal_error.value.details == {"from": "SUCCEEDED", "to": "READY"}


def test_success_requires_a_minimal_hashed_output_receipt():
    started_at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    pending = TaskNodeRuntime(input_digest=canonical_payload_digest({"period": "current_month"}))
    ready = transition_node_runtime(pending, TaskNodeStatus.READY, at=started_at)
    running = transition_node_runtime(ready, TaskNodeStatus.RUNNING, at=started_at)

    with pytest.raises(TaskNodeTransitionError) as missing_receipt:
        transition_node_runtime(running, TaskNodeStatus.SUCCEEDED)
    assert missing_receipt.value.code == "TASK_NODE_RECEIPT_REQUIRED"
    assert "不能声明执行成功" in missing_receipt.value.message

    output = {"transaction_id": "txn_demo_001", "account_number": "not-persisted"}
    receipt = create_output_receipt(
        kind=TaskNodeReceiptKind.TOOL_RESULT,
        output=output,
        summary="银行沙箱返回交易回执",
        reference_id="txn_demo_001",
    )
    succeeded = transition_node_runtime(
        running,
        TaskNodeStatus.SUCCEEDED,
        output_receipt=receipt,
        at=started_at + timedelta(seconds=2),
    )

    assert succeeded.finished_at == started_at + timedelta(seconds=2)
    assert succeeded.output_receipt.output_digest == canonical_payload_digest(output)
    assert "account_number" not in succeeded.output_receipt.model_dump_json()


def test_failed_node_records_error_code_and_safe_reason():
    started_at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    running = TaskNodeRuntime(
        status=TaskNodeStatus.RUNNING,
        revision=2,
        attempt=1,
        input_digest=canonical_payload_digest({}),
        started_at=started_at,
    )
    failed = transition_node_runtime(
        running,
        TaskNodeStatus.FAILED,
        reason="BANK_TOOL_UNAVAILABLE",
        error_code="BANK_TOOL_UNAVAILABLE",
        at=started_at + timedelta(seconds=3),
    )

    assert failed.status == TaskNodeStatus.FAILED
    assert failed.error_code == "BANK_TOOL_UNAVAILABLE"
    assert failed.finished_at == started_at + timedelta(seconds=3)
    assert failed.output_receipt is None


def test_node_input_digest_detects_parameter_tampering():
    node = TaskNode.model_validate({
        "id": "n1",
        "type": "TOOL",
        "label": "生成转账预览",
        "tool": "transfer.preview",
        "input": {"amount": 200, "recipient": "小王"},
    })
    tampered = node.model_dump(mode="json")
    tampered["input"]["amount"] = 2000

    with pytest.raises(ValidationError, match="TASK_NODE_INPUT_TAMPERED"):
        TaskNode.model_validate(tampered)


def test_digest_is_stable_for_equivalent_key_order():
    assert canonical_payload_digest({"recipient": "小王", "amount": 200}) == canonical_payload_digest(
        {"amount": 200, "recipient": "小王"}
    )


def plan_with(nodes: list[dict]) -> TaskGraphPlan:
    return TaskGraphPlan(plan_id="plan_validation", nodes=nodes)


@pytest.mark.parametrize(
    ("nodes", "code"),
    [
        (
            [
                {"id": "n1", "type": "RESPONSE", "label": "响应一"},
                {"id": "n1", "type": "RESPONSE", "label": "响应二"},
            ],
            "TASK_GRAPH_NODE_ID_DUPLICATE",
        ),
        (
            [
                {"id": "n1", "type": "RESPONSE", "label": "响应", "depends_on": ["missing"]},
            ],
            "TASK_GRAPH_DEPENDENCY_NOT_FOUND",
        ),
        (
            [
                {"id": "n1", "type": "RESPONSE", "label": "响应", "depends_on": ["n1"]},
            ],
            "TASK_GRAPH_SELF_DEPENDENCY",
        ),
        (
            [
                {"id": "n1", "type": "RESPONSE", "label": "响应"},
                {"id": "n2", "type": "RESPONSE", "label": "结束", "depends_on": ["n1", "n1"]},
            ],
            "TASK_GRAPH_DEPENDENCY_DUPLICATE",
        ),
        (
            [
                {"id": "n1", "type": "RESPONSE", "label": "响应", "depends_on": ["n2"]},
                {"id": "n2", "type": "RESPONSE", "label": "结束", "depends_on": ["n1"]},
            ],
            "TASK_GRAPH_CYCLE_DETECTED",
        ),
        (
            [
                {"id": "n1", "type": "TOOL", "label": "危险工具", "tool": "shell.execute"},
            ],
            "TASK_GRAPH_TOOL_NOT_ALLOWED",
        ),
        (
            [
                {"id": "n1", "type": "TOOL", "label": "缺少工具"},
            ],
            "TASK_GRAPH_TOOL_REQUIRED",
        ),
        (
            [
                {
                    "id": "n1",
                    "type": "RESPONSE",
                    "label": "伪装响应节点",
                    "tool": "transaction.list",
                },
            ],
            "TASK_GRAPH_TOOL_FORBIDDEN_FOR_NODE_TYPE",
        ),
    ],
)
def test_invalid_graph_structure_is_rejected_with_stable_error(nodes, code):
    with pytest.raises(TaskNodeTransitionError) as captured:
        validate_task_graph(plan_with(nodes))

    assert captured.value.code == code
    assert captured.value.message.endswith("不能进入执行阶段。")


def test_protected_write_requires_policy_ancestor():
    graph = plan_with([
        {"id": "n1", "type": "HUMAN_CONFIRM", "label": "等待确认"},
        {
            "id": "n2",
            "type": "TOOL",
            "label": "执行转账",
            "tool": "transfer.execute",
            "depends_on": ["n1"],
        },
    ])

    with pytest.raises(TaskNodeTransitionError) as captured:
        validate_task_graph(graph)
    assert captured.value.code == "TASK_GRAPH_WRITE_POLICY_REQUIRED"
    assert "缺少前置策略节点" in captured.value.message


def test_protected_write_requires_human_authorization_ancestor():
    graph = plan_with([
        {
            "id": "n1",
            "type": "POLICY",
            "label": "风险评估",
            "tool": "policy.evaluate",
        },
        {
            "id": "n2",
            "type": "TOOL",
            "label": "执行转账",
            "tool": "transfer.execute",
            "depends_on": ["n1"],
        },
    ])

    with pytest.raises(TaskNodeTransitionError) as captured:
        validate_task_graph(graph)
    assert captured.value.code == "TASK_GRAPH_WRITE_AUTHORIZATION_REQUIRED"
    assert "缺少前置人工授权节点" in captured.value.message


def test_protected_write_accepts_policy_and_authorization_dependency_path():
    graph = plan_with([
        {
            "id": "n1",
            "type": "POLICY",
            "label": "风险评估",
            "tool": "policy.evaluate",
        },
        {
            "id": "n2",
            "type": "HUMAN_CONFIRM",
            "label": "等待确认",
            "depends_on": ["n1"],
        },
        {
            "id": "n3",
            "type": "TOOL",
            "label": "执行转账",
            "tool": "transfer.execute",
            "depends_on": ["n2"],
        },
    ])

    assert validate_task_graph(graph) is graph


def test_life_event_plan_places_policy_and_confirmation_before_formal_write():
    plan = build_plan(parse_intent("我爱人11月18日生日，帮我留1000元买礼物"))
    nodes = {node["id"]: node for node in plan["nodes"]}

    assert nodes["n3"]["type"] == "POLICY"
    assert nodes["n4"]["type"] == "HUMAN_CONFIRM"
    assert nodes["n5"]["tool"] == "life_graph.change_request.decide"
    assert nodes["n5"]["depends_on"] == ["n4"]
