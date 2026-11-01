"""稳定的应用错误契约，避免向客户端暴露内部异常细节。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorDefinition:
    status_code: int
    message: str
    retryable: bool = False


ERROR_CATALOG: dict[str, ErrorDefinition] = {
    "INVALID_REQUEST": ErrorDefinition(422, "请求参数不完整或格式不正确。"),
    "INTERNAL_ERROR": ErrorDefinition(500, "服务暂时不可用，请稍后重试。", True),
    "RESOURCE_FORBIDDEN": ErrorDefinition(403, "你无权访问或操作该资源。"),
    "SESSION_NOT_FOUND": ErrorDefinition(404, "Agent 会话不存在或无权访问。"),
    "RUN_NOT_FOUND": ErrorDefinition(404, "Agent 任务不存在或无权访问。"),
    "CONFIRMATION_NOT_FOUND": ErrorDefinition(404, "待确认操作不存在或无权访问。"),
    "VERIFICATION_NOT_FOUND": ErrorDefinition(404, "强验证挑战不存在或无权访问。"),
    "REPLAY_DETECTED": ErrorDefinition(409, "该操作已经处理，不能重复提交。"),
    "RUN_NOT_CONFIRMABLE": ErrorDefinition(409, "当前任务不处于可确认状态。"),
    "RUN_NOT_VERIFIABLE": ErrorDefinition(409, "当前任务不处于可验证状态。"),
    "RUN_NOT_HANDOFFABLE": ErrorDefinition(409, "当前任务已结束，不能再转入人工处理。"),
    "INVALID_TASK_NODE_TRANSITION": ErrorDefinition(
        409, "任务节点状态转换不合法，流程已停止自动推进。"
    ),
    "TASK_NODE_STATUS_REASON_REQUIRED": ErrorDefinition(
        409, "任务节点缺少等待、失败或跳过原因，流程已停止自动推进。"
    ),
    "TASK_NODE_INPUT_DIGEST_REQUIRED": ErrorDefinition(
        409, "任务节点缺少输入摘要，不能开始执行。"
    ),
    "TASK_NODE_INPUT_TAMPERED": ErrorDefinition(
        409, "任务节点输入与已保存摘要不一致，流程已停止自动推进。"
    ),
    "TASK_NODE_RECEIPT_REQUIRED": ErrorDefinition(
        409, "任务节点缺少可信输出回执，不能声明执行成功。"
    ),
    "TASK_NODE_ERROR_CODE_REQUIRED": ErrorDefinition(
        409, "任务节点失败但缺少稳定错误码，流程已停止自动推进。"
    ),
    "TASK_NODE_ERROR_CODE_NOT_ALLOWED": ErrorDefinition(
        409, "非失败节点不能携带错误码，流程已停止自动推进。"
    ),
    "TASK_NODE_TIME_INVALID": ErrorDefinition(
        409, "任务节点时间证据不合法，流程已停止自动推进。"
    ),
    "TASK_NODE_EVIDENCE_INVALID": ErrorDefinition(
        409, "任务节点执行证据不完整或不一致，流程已停止自动推进。"
    ),
    "TASK_GRAPH_NODE_ID_DUPLICATE": ErrorDefinition(
        409, "任务图包含重复节点编号，不能进入执行阶段。"
    ),
    "TASK_GRAPH_DEPENDENCY_DUPLICATE": ErrorDefinition(
        409, "任务节点包含重复依赖，不能进入执行阶段。"
    ),
    "TASK_GRAPH_DEPENDENCY_NOT_FOUND": ErrorDefinition(
        409, "任务节点引用了不存在的依赖，不能进入执行阶段。"
    ),
    "TASK_GRAPH_SELF_DEPENDENCY": ErrorDefinition(
        409, "任务节点不能依赖自身，不能进入执行阶段。"
    ),
    "TASK_GRAPH_CYCLE_DETECTED": ErrorDefinition(
        409, "任务图存在循环依赖，不能进入执行阶段。"
    ),
    "TASK_GRAPH_TOOL_REQUIRED": ErrorDefinition(
        409, "工具或策略节点缺少工具名称，不能进入执行阶段。"
    ),
    "TASK_GRAPH_TOOL_NOT_ALLOWED": ErrorDefinition(
        409, "任务图引用了未授权工具，不能进入执行阶段。"
    ),
    "TASK_GRAPH_TOOL_FORBIDDEN_FOR_NODE_TYPE": ErrorDefinition(
        409, "当前节点类型不能调用工具，不能进入执行阶段。"
    ),
    "TASK_GRAPH_WRITE_POLICY_REQUIRED": ErrorDefinition(
        409, "受保护写操作缺少前置策略节点，不能进入执行阶段。"
    ),
    "TASK_GRAPH_WRITE_AUTHORIZATION_REQUIRED": ErrorDefinition(
        409, "受保护写操作缺少前置人工授权节点，不能进入执行阶段。"
    ),
    "TASK_GRAPH_HANDLER_NOT_REGISTERED": ErrorDefinition(
        409, "任务节点没有已注册的后端处理器，流程已停止自动推进。"
    ),
    "TASK_GRAPH_HANDLER_RESULT_INVALID": ErrorDefinition(
        409, "任务节点返回了无效结果，流程已停止自动推进。"
    ),
    "TASK_GRAPH_NODE_EXECUTION_FAILED": ErrorDefinition(
        503, "任务节点执行失败，系统未声明业务操作成功。", True
    ),
    "TASK_GRAPH_RECOVERY_AUTHORIZATION_MISSING": ErrorDefinition(
        409, "流程恢复缺少可信授权依据，已停止自动推进。"
    ),
    "HANDOFF_PREPARATION_FAILED": ErrorDefinition(
        409, "冻结自动流程失败，任务未转入人工处理，请刷新后重试。", True
    ),
    "WORKFLOW_RECOVERY_EVIDENCE_MISSING": ErrorDefinition(
        409, "流程恢复缺少可信依据，已停止自动推进并等待人工处理。"
    ),
    "LIFE_GRAPH_EXECUTION_FAILED": ErrorDefinition(
        503, "生活图谱写入失败，系统未声明候选方案已生效。", True
    ),
    "VERIFICATION_NOT_PENDING": ErrorDefinition(409, "该验证挑战已不处于待验证状态。"),
    "VERIFICATION_EXPIRED": ErrorDefinition(409, "验证码已过期，请重新获取。"),
    "MFA_RESEND_COOLDOWN": ErrorDefinition(409, "验证码发送过于频繁，请稍后重试。", True),
    "DEMO_CODE_UNAVAILABLE": ErrorDefinition(409, "当前演示验证码不可用，请重新发送。"),
    "CHANGE_REQUEST_NOT_FOUND": ErrorDefinition(404, "候选变更不存在或无权访问。"),
    "SOURCE_RUN_NOT_FOUND": ErrorDefinition(404, "关联的 Agent 任务不存在。"),
    "LIFE_GRAPH_NODE_NOT_FOUND": ErrorDefinition(404, "生活图谱节点不存在或无权访问。"),
    "CHANGE_REQUEST_REPLAYED": ErrorDefinition(409, "该候选变更已经处理，不能重复确认。"),
    "CHANGE_REQUEST_NOT_PENDING": ErrorDefinition(409, "该候选变更已不处于待确认状态。"),
    "CHANGE_REQUEST_EXPIRED": ErrorDefinition(409, "候选变更已过期，请重新生成。"),
    "CHANGE_REQUEST_TAMPERED": ErrorDefinition(409, "候选内容与原始快照不一致，操作已停止。"),
    "CHANGE_SET_SCHEMA_INVALID": ErrorDefinition(409, "候选内容结构不合法，操作已停止。"),
    "CHANGE_SET_CONFLICT": ErrorDefinition(409, "候选内容与当前图谱冲突，请刷新后重试。"),
    "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_CONTENT": ErrorDefinition(
        409, "同一幂等键不能用于不同内容。"
    ),
    "NODE_ALREADY_EXISTS": ErrorDefinition(409, "候选节点已经存在，请刷新图谱。"),
    "CHANGE_SET_REFERENCE_NOT_FOUND": ErrorDefinition(409, "候选内容引用了不存在的节点。"),
    "EVENT_NODE_TYPE_MISMATCH": ErrorDefinition(409, "事件信息与节点类型不匹配。"),
    "GOAL_NODE_TYPE_MISMATCH": ErrorDefinition(409, "资金目标与节点类型不匹配。"),
    "ACTION_EVENT_NOT_FOUND": ErrorDefinition(409, "提醒动作引用的事件不存在。"),
    "ACTION_GOAL_NOT_FOUND": ErrorDefinition(409, "提醒动作引用的资金目标不存在。"),
    "PROTECTED_BALANCE_OUT_OF_RANGE": ErrorDefinition(400, "保护金额不能小于零。"),
    "HORIZON_MONTHS_OUT_OF_RANGE": ErrorDefinition(400, "分析周期必须在 1 至 24 个月之间。"),
    "LIFE_GRAPH_NODE_INACTIVE": ErrorDefinition(409, "该节点已经停用，不能继续修改。"),
    "EVENT_CORRECTION_TYPE_MISMATCH": ErrorDefinition(409, "只有事件节点可以修改事件日期。"),
    "EVENT_RECORD_NOT_FOUND": ErrorDefinition(409, "事件节点缺少对应的事件记录。"),
    "GOAL_CORRECTION_TYPE_MISMATCH": ErrorDefinition(409, "只有资金目标节点可以修改目标金额。"),
    "GOAL_RECORD_NOT_FOUND": ErrorDefinition(409, "目标节点缺少对应的资金记录。"),
    "TARGET_AMOUNT_BELOW_RESERVED": ErrorDefinition(409, "目标金额不能低于已经预留的金额。"),
    "CORRECTION_IDEMPOTENCY_CONFLICT": ErrorDefinition(409, "该纠正请求的幂等键已用于其他内容。"),
    "LIFE_GRAPH_NOT_CONFIGURED": ErrorDefinition(503, "生活关系图服务尚未配置。", True),
    "LIFE_GOAL_CONFLICT_SERVICE_NOT_CONFIGURED": ErrorDefinition(
        503, "资金目标冲突检测服务尚未配置。", True
    ),
    "CHANNEL_GATEWAY_NOT_CONFIGURED": ErrorDefinition(
        503, "Channel Gateway 尚未配置。", True
    ),
    "CHANNEL_GATEWAY_UNAUTHORIZED": ErrorDefinition(401, "渠道适配器认证失败。"),
    "CHANNEL_LINK_TOKEN_INVALID": ErrorDefinition(400, "渠道绑定码无效。"),
    "CHANNEL_LINK_TOKEN_EXPIRED": ErrorDefinition(409, "渠道绑定码已过期，请重新生成。"),
    "CHANNEL_LINK_TOKEN_REPLAYED": ErrorDefinition(409, "渠道绑定码已经使用，不能重复兑换。"),
    "CHANNEL_IDENTITY_ALREADY_BOUND": ErrorDefinition(
        409, "该渠道身份已绑定其他用户，不能重复绑定。"
    ),
    "CHANNEL_IDENTITY_NOT_FOUND": ErrorDefinition(404, "渠道身份不存在或无权访问。"),
    "CHANNEL_BINDING_REQUIRED": ErrorDefinition(403, "请先在受信银行页面完成渠道绑定。"),
    "CHANNEL_MESSAGE_CONFLICT": ErrorDefinition(
        409, "相同渠道消息编号对应了不同内容，已拒绝处理。"
    ),
    "CHANNEL_MESSAGE_IN_PROGRESS": ErrorDefinition(
        409, "该渠道消息正在处理中，请稍后查询。", True
    ),
    "CHANNEL_MESSAGE_PREVIOUSLY_FAILED": ErrorDefinition(
        409, "该渠道消息此前处理失败，未自动重复执行。", True
    ),
    "CHANNEL_PROCESSING_FAILED": ErrorDefinition(
        503, "渠道消息处理失败，系统未声明业务执行成功。", True
    ),
    "CHANNEL_WORKER_ID_CONFLICT": ErrorDefinition(
        409, "Worker 标识已用于其他渠道类型，已拒绝覆盖心跳。"
    ),
    "FEISHU_WEBHOOK_NOT_CONFIGURED": ErrorDefinition(
        503, "飞书 Webhook 尚未完成验签与加密配置。", True
    ),
    "FEISHU_WEBHOOK_SIGNATURE_INVALID": ErrorDefinition(
        401, "飞书 Webhook 签名校验失败。"
    ),
    "FEISHU_WEBHOOK_TIMESTAMP_INVALID": ErrorDefinition(
        401, "飞书 Webhook 请求时间超出允许范围。"
    ),
    "FEISHU_WEBHOOK_TOKEN_INVALID": ErrorDefinition(
        401, "飞书 Webhook 验证令牌不匹配。"
    ),
    "FEISHU_WEBHOOK_PAYLOAD_INVALID": ErrorDefinition(
        400, "飞书 Webhook 事件格式不正确。"
    ),
    "SCHEDULED_ACTION_NOT_FOUND": ErrorDefinition(404, "提醒动作不存在或无权访问。"),
    "SCHEDULED_ACTION_NOT_CONTROLLABLE": ErrorDefinition(
        409, "该提醒已结束或等待人工处理，不能执行当前操作。"
    ),
    "SCHEDULED_ACTION_IN_PROGRESS": ErrorDefinition(
        409, "该提醒正在由调度 Worker 处理，请稍后刷新。", True
    ),
    "SCHEDULED_ACTION_LEASE_LOST": ErrorDefinition(
        409, "提醒处理租约已经失效，当前 Worker 已停止写入结果。", True
    ),
    "LIFE_RADAR_NOT_CONFIGURED": ErrorDefinition(
        503, "生活事件雷达尚未完成数据库、渠道密钥或飞书配置。", True
    ),
    "LIFE_RADAR_SIMULATION_DISABLED": ErrorDefinition(
        403, "当前环境未开启沙箱模拟时间。"
    ),
    "FEISHU_DELIVERY_ID_UNAVAILABLE": ErrorDefinition(
        409, "当前账号缺少可用的飞书私聊投递身份，请重新完成飞书绑定。"
    ),
    "FEISHU_PROACTIVE_SEND_FAILED": ErrorDefinition(
        503, "飞书主动提醒发送失败，系统未声明通知成功。", True
    ),
    "FUNDING_NEGOTIATION_NOT_CONFIGURED": ErrorDefinition(
        503, "家庭资金协商服务尚未配置数据库。", True
    ),
    "FUNDING_NEGOTIATION_NOT_FOUND": ErrorDefinition(404, "资金协商记录不存在或无权访问。"),
    "FUNDING_IDEMPOTENCY_CONFLICT": ErrorDefinition(409, "该幂等键已用于另一项资金协商。"),
    "FUNDING_NEGOTIATION_NOT_SELECTABLE": ErrorDefinition(409, "当前协商不处于可选择方案状态。"),
    "FUNDING_NEGOTIATION_NOT_CONFIRMABLE": ErrorDefinition(409, "当前协商不处于可确认状态。"),
    "FUNDING_NEGOTIATION_NOT_COMPLETABLE": ErrorDefinition(409, "当前协商不处于约束补充状态。"),
    "FUNDING_NEGOTIATION_NOT_CONTROLLABLE": ErrorDefinition(409, "当前协商已经结束，不能执行该控制操作。"),
    "FUNDING_NEGOTIATION_NOT_UNDOABLE": ErrorDefinition(409, "当前协商没有可撤销的已应用变更。"),
    "FUNDING_NEGOTIATION_EXPIRED": ErrorDefinition(409, "资金协商已经过期，请重新计算。"),
    "FUNDING_PLAN_NOT_FOUND": ErrorDefinition(404, "所选资金方案不存在或已经失效。"),
    "FUNDING_PLAN_INFEASIBLE": ErrorDefinition(409, "不可行方案不能进入确认阶段。"),
    "FUNDING_MODEL_PLAN_REJECTED": ErrorDefinition(409, "模型建议未通过确定性资金约束校验，已丢弃。"),
    "FUNDING_SNAPSHOT_CHANGED": ErrorDefinition(
        409, "余额或目标已变化，旧方案已失效并按最新数据重新计算。", True
    ),
    "FUNDING_UNDO_CONFLICT": ErrorDefinition(409, "目标在应用后又发生变化，不能自动撤销，请转人工处理。"),
    "FUNDING_ACCOUNT_NOT_FOUND": ErrorDefinition(404, "允许使用的账户不存在或无权访问。"),
    "FUNDING_CURRENCY_NOT_SUPPORTED": ErrorDefinition(409, "当前协商仅支持人民币账户。"),
    "FUNDING_BANK_INCOME_UNVERIFIED": ErrorDefinition(409, "银行确认收入缺少可核验的入账流水，不能进入保证方案。"),
    "FUNDING_GOAL_DUE_DATE_REQUIRED": ErrorDefinition(409, "资金目标缺少截止日期，请先补充。"),
    "FUNDING_CONSENT_SCOPE_NOT_FOUND": ErrorDefinition(404, "资金调整授权范围不存在或无权访问。"),
    "FUNDING_CONSENT_SCOPE_NOT_ACTIVE": ErrorDefinition(409, "资金调整授权范围已经失效或撤销。"),
    "FUNDING_CONSENT_SCOPE_EXPIRED": ErrorDefinition(409, "资金调整授权范围已经到期。"),
    "GUARDIAN_USER_NOT_FOUND": ErrorDefinition(404, "指定的守护用户不存在或已经停用。"),
    "GUARDIAN_CIRCLE_NOT_CONFIGURED": ErrorDefinition(503, "家庭守护圈需要启用数据库。", True),
    "GUARDIAN_SELF_INVITATION_FORBIDDEN": ErrorDefinition(409, "不能邀请自己成为自己的守护人。"),
    "GUARDIAN_INVITATION_INVALID": ErrorDefinition(400, "守护邀请令牌无效。"),
    "GUARDIAN_INVITATION_NOT_FOUND": ErrorDefinition(404, "守护邀请不存在或无权访问。"),
    "GUARDIAN_INVITATION_ALREADY_PENDING": ErrorDefinition(409, "双方已有待处理的守护邀请。"),
    "GUARDIAN_INVITATION_NOT_PENDING": ErrorDefinition(409, "守护邀请已不处于待处理状态。"),
    "GUARDIAN_INVITATION_EXPIRED": ErrorDefinition(409, "守护邀请已经过期，请重新创建。"),
    "GUARDIAN_INVITATION_REPLAYED": ErrorDefinition(409, "守护邀请已经处理，不能重复使用。"),
    "GUARDIAN_CONSENT_ALREADY_ACTIVE": ErrorDefinition(409, "双方已有生效中的守护授权。"),
    "GUARDIAN_CONSENT_VALIDITY_INVALID": ErrorDefinition(422, "守护授权有效期必须在十分钟至一年之间。"),
    "GUARDIAN_CONSENT_NOT_FOUND": ErrorDefinition(404, "守护授权不存在或无权访问。"),
    "GUARDIAN_CONSENT_NOT_ACTIVE": ErrorDefinition(409, "守护授权已撤销或到期，不能继续查看或确认。"),
    "GUARDIAN_SCOPE_VIOLATION": ErrorDefinition(403, "当前风险请求超出已同意的场景或金额范围。"),
    "GUARDIAN_SENSITIVE_FIELD_FORBIDDEN": ErrorDefinition(422, "守护摘要不能包含未脱敏的账号或连续敏感数字。"),
    "GUARDIAN_RISK_REVIEW_NOT_FOUND": ErrorDefinition(404, "守护风险确认不存在或无权访问。"),
    "GUARDIAN_RISK_REVIEW_NOT_OWNER_CONFIRMABLE": ErrorDefinition(409, "当前风险请求已不等待本人确认。"),
    "GUARDIAN_RISK_REVIEW_NOT_GUARDIAN_CONFIRMABLE": ErrorDefinition(409, "当前风险请求尚未由本人确认或已经结束。"),
    "FRAUD_EVENT_NOT_FOUND": ErrorDefinition(404, "反诈风险事件不存在或无权访问。"),
    "FRAUD_EVENT_NOT_ACTIVE": ErrorDefinition(409, "该反诈冷静期已经解除或到期。"),
    "FRAUD_APPEAL_ALREADY_SUBMITTED": ErrorDefinition(409, "该风险事件已经提交过误报申诉。"),
    "FRAUD_COOLING_OFF_ACTIVE": ErrorDefinition(
        409, "同一收款人仍处于反诈冷静期，本次资金请求已阻断。"
    ),
    "FRAUD_RELEASE_CHANNEL_FORBIDDEN": ErrorDefinition(
        403, "冷静期只能在受信银行 App 中通过强验证解除，飞书或其他 IM 无权解除。"
    ),
    "FRAUD_RELEASE_CHALLENGE_NOT_FOUND": ErrorDefinition(404, "冷静期释放验证不存在或无权访问。"),
    "FRAUD_RELEASE_CHALLENGE_NOT_PENDING": ErrorDefinition(409, "冷静期释放验证已处理，不能重复提交。"),
    "FRAUD_RELEASE_CHALLENGE_EXPIRED": ErrorDefinition(409, "冷静期释放验证码已过期，请重新获取。"),
    "FRAUD_RELEASE_VERIFICATION_FAILED": ErrorDefinition(409, "冷静期释放验证码不正确。"),
    "FRAUD_RELEASE_VERIFICATION_LOCKED": ErrorDefinition(
        409, "冷静期释放验证连续失败，当前挑战已锁定。"
    ),
}


def error_definition(code: str) -> ErrorDefinition:
    if code in ERROR_CATALOG:
        return ERROR_CATALOG[code]
    if code.endswith("NOT_FOUND"):
        return ErrorDefinition(404, "请求的资源不存在。")
    if code == "RESOURCE_FORBIDDEN":
        return ERROR_CATALOG[code]
    return ErrorDefinition(409, "当前状态下无法完成该请求。")


class ApplicationError(Exception):
    def __init__(
        self,
        code: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        definition = error_definition(code)
        self.code = code
        self.status_code = definition.status_code
        self.message = definition.message
        self.retryable = definition.retryable
        self.details = details or {}

    def public_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            detail["details"] = self.details
        return detail
