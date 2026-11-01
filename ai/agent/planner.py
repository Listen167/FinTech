"""把结构化意图转换为可展示、可审计的任务 DAG。"""

from __future__ import annotations

from uuid import uuid4

from ai.agent.task_graph import TaskGraphPlan, validate_task_graph


PLANNER_VERSION = "deterministic-dag-2.0.0"


def _node(node_id: str, node_type: str, label: str, **extra: object) -> dict:
    return {"id": node_id, "type": node_type, "label": label, **extra}


def build_plan(intent: dict) -> dict:
    name = intent["name"]
    slots = intent.get("slots", {})
    nodes: list[dict] = []

    if name == "bill.analyze":
        nodes = [
            _node("n1", "TOOL", "读取本月交易", tool="transaction.list", input={}),
            _node("n2", "TOOL", "生成账单洞察", tool="bill.analyze", depends_on=["n1"]),
            _node("n3", "RESPONSE", "呈现异常与建议", depends_on=["n2"]),
        ]
    elif name == "subscription.list":
        nodes = [
            _node("n1", "TOOL", "读取订阅代扣", tool="subscription.list", input={}),
            _node("n2", "RESPONSE", "标记闲置订阅", depends_on=["n1"]),
        ]
    elif name == "subscription.cancel":
        nodes = [
            _node("n1", "TOOL", "定位目标订阅", tool="subscription.list", input=slots),
            _node("n2", "TOOL", "生成取消预览", tool="subscription.cancel.preview", depends_on=["n1"]),
            _node("n3", "POLICY", "黄色权限检查", tool="policy.evaluate", depends_on=["n2"]),
            _node("n4", "HUMAN_CONFIRM", "等待用户确认", depends_on=["n3"]),
            _node("n5", "TOOL", "取消订阅", tool="subscription.cancel.execute", depends_on=["n4"]),
        ]
    elif name == "transfer.create":
        nodes = [
            _node("n1", "TOOL", "查找收款人", tool="contacts.search", input=slots),
            _node("n2", "TOOL", "生成不可变转账预览", tool="transfer.preview", depends_on=["n1"]),
            _node("n3", "POLICY", "动态风险评估", tool="policy.evaluate", depends_on=["n2"]),
            _node("n4", "HUMAN_CONFIRM", "确认或强验证", depends_on=["n3"]),
            _node("n5", "TOOL", "执行转账", tool="transfer.execute", depends_on=["n4"]),
            _node("n6", "RESPONSE", "返回银行流水与审计编号", depends_on=["n5"]),
        ]
    elif name == "finance.cross_scenario":
        nodes = [
            _node("n1", "TOOL", "读取账单", tool="transaction.list", input={}),
            _node("n2", "TOOL", "分析消费与异常", tool="bill.analyze", depends_on=["n1"]),
            _node("n3", "TOOL", "识别订阅代扣", tool="subscription.list", depends_on=["n1"]),
            _node("n4", "POLICY", "评估后续写操作", tool="policy.evaluate", depends_on=["n2", "n3"]),
            _node("n5", "RESPONSE", "返回跨场景财务体检", depends_on=["n4"]),
        ]
    elif name == "life.event_plan":
        nodes = [
            _node("n1", "VALIDATION", "校验关系、事件日期与目标金额", input=slots),
            _node(
                "n2",
                "TOOL",
                "生成生活图谱候选变更",
                tool="life_graph.change_request.create",
                depends_on=["n1"],
            ),
            _node(
                "n3",
                "POLICY",
                "分析目标资金冲突与写入边界",
                tool="policy.evaluate",
                depends_on=["n2"],
            ),
            _node("n4", "HUMAN_CONFIRM", "等待用户核对关系、事件和目标", depends_on=["n3"]),
            _node(
                "n5",
                "TOOL",
                "确认后原子写入生活图谱",
                tool="life_graph.change_request.decide",
                depends_on=["n4"],
            ),
        ]
    else:
        nodes = [_node("n1", "RESPONSE", "询问用户需要办理的银行业务")]

    plan = validate_task_graph(TaskGraphPlan(
        plan_id=f"plan_{uuid4().hex[:10]}",
        nodes=nodes,
    ))
    return plan.model_dump(mode="json", exclude_none=True)

