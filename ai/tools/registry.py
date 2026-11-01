ALLOWED_TOOLS = frozenset({
    "account.list", # 列出账户
    "account.balance.get",  # 获取账户余额
    "transaction.list",  # 列出交易流水
    "bill.summary",  # 账单汇总/摘要
    "bill.analyze",  # 账单分析
    "contacts.search",  # 搜索联系人
    "transfer.preview",  # 转账预览
    "transfer.execute",  # 执行转账
    "subscription.list",  # 列出订阅
    "subscription.cancel.preview",  # 取消订阅预览
    "subscription.cancel.execute",  # 执行取消订阅
    "security.otp.send",  # 发送OTP验证码
    "security.otp.verify",  # 校验OTP验证码
    "audit.create",  # 创建审计记录
    "safety.evaluate",  # 安全评估和风险预测
    "policy.evaluate",  # 确定性权限与风险策略评估
    "life_graph.change_request.create",  # 创建生活图谱候选变更
    "life_graph.change_request.decide",  # 确认后应用生活图谱候选变更
})

# 候选、预览和只读分析不属于受保护业务写入；真正改变业务事实的工具必须经过策略和人工授权节点。
PROTECTED_WRITE_TOOLS = frozenset({
    "transfer.execute",
    "subscription.cancel.execute",
    "life_graph.change_request.decide",
})


def is_allowed_tool(name: str) -> bool:
    return name in ALLOWED_TOOLS


def is_protected_write_tool(name: str) -> bool:
    return name in PROTECTED_WRITE_TOOLS

