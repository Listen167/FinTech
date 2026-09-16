ALLOWED_TOOLS = {
    "account.list", # 列出账户
    "account.balance.get",  # 获取账户余额
    "transaction.list",  # 列出交易流水
    "bill.summary",  # 账单汇总/摘要
    "bill.analyze",  # 账单分析
    "contacts.search",  # 搜索联系人
    "transfer.preview",  # 账单预览
    "transfer.execute",  # 执行订阅
    "subscription.list",  # 列出订阅
    "subscription.cancel.preview",  # 取消订阅预览
    "subscription.cancel.execute",  # 执行取消订阅
    "security.otp.send",  # 发送OTP验证码
    "security.otp.verify",  # 校验OTP验证码
    "audit.create",  # 创造审计记录
    "safety.evaluate", # 安全评估和风险预测
}


def is_allowed_tool(name: str) -> bool:
    return name in ALLOWED_TOOLS

