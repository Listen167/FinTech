# 反诈冷静期设计与实现

## 1. 目标

普通关键词拦截只对当前一句话生效。用户受到诈骗分子诱导后，可能换一种说法、降低金额或重新建立会话，继续向同一收款人转账。

反诈冷静期把一次高风险判断升级为有时效的风险事件：

- 关联当前用户和收款人 HMAC 指纹，不保存可逆的完整收款标识到前端。
- 在冷静期内阻断同一收款人的后续资金请求，不冻结其他收款人或整个账户。
- 展示确定性命中证据、结束时间和安全求助入口。
- 误报申诉只记录事实，不会自动解除风险事件。
- 解除必须回到受信银行 App 完成 MFA；飞书和其他 IM 无权解除。
- 解除只终止冷静期，原转账不会自动恢复或执行。

## 2. 责任边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| DeepSeek/本地解析器 | 提取转账意图、收款人和诈骗语义线索 | 决定是否解除冷静期 |
| Safety Guard | 检测安全账户、冒充、紧迫、保密、远程控制等信号 | 直接执行资金操作 |
| Policy Engine | 计算确定性风险分数和风险因子 | 接受前端降低风险等级 |
| FraudCoolingService | 生成收款人指纹、创建/复用风险事件、控制申诉和 MFA 释放 | 冻结全账户或自动恢复原转账 |
| Agent Runtime | 在策略节点和转账执行节点检查活动冷静期 | 绕过快照、确认或 MFA |
| 用户 | 核实事实、提交申诉、在受信 App 完成强验证 | 通过飞书文本直接解除 |

## 3. 触发规则

语义信号来自 `ai/safety/guard.py`，当前包括：

| 信号 | 事实含义 |
| --- | --- |
| `SAFE_ACCOUNT` | 安全账户、监管账户、核查账户 |
| `IMPERSONATION` | 冒充公检法、平台客服或权威机构 |
| `URGENCY` | 立即转账、限时冻结等紧迫话术 |
| `SECRECY` | 要求保密或不告知家人 |
| `REMOTE_CONTROL` | 屏幕共享、远程控制或控制软件 |

满足下列任一确定性组合时创建 `COOLING_OFF` 事件：

1. 命中 `SAFE_ACCOUNT` 或 `REMOTE_CONTROL`。
2. 同时命中至少两个诈骗语义信号。
3. 命中任一诈骗信号，同时为新收款人且金额超过 1000 元。

已有活动事件时不重新分析措辞，只要当前用户和收款人指纹相同就直接阻断，并增加 `retry_count`。冷静期不会因重试自动延长。

## 4. 收款人指纹

指纹输入为：

```text
recipient-v1 | owner_user_id | 规范化收款人名称 | 脱敏账号
```

使用 `HMAC-SHA256` 计算，密钥优先读取 `FRAUD_RECIPIENT_PEPPER`，未填写时使用 `JWT_SECRET_KEY` 做域隔离 HMAC。指纹包含用户域，因此不能用来跨用户关联同名收款人。

前端只显示类似 `12ab34cd...89ef01` 的提示，不返回完整指纹。数据库仅保存消息摘要，不保存原始聊天文本到风险事件表。

## 5. 状态与数据表

迁移版本：`20261101_10`

| 表 | 用途 |
| --- | --- |
| `fraud_risk_events` | 风险事件、用户、收款人指纹、信号、证据、结束时间、申诉和释放状态 |
| `fraud_release_challenges` | 受信 App MFA 挑战、HMAC 摘要、失败次数、过期与锁定状态 |
| `fraud_risk_audits` | 创建、关联重试、申诉、挑战、失败、到期和释放审计 |

风险事件状态：

```text
COOLING_OFF -> RELEASED
COOLING_OFF -> EXPIRED
```

活动事件使用唯一 `active_scope_key` 保证同一用户与收款人最多存在一个活动冷静期。MySQL 写入使用事务和行锁；并发创建冲突时复用已有事件。

释放挑战状态：

```text
PENDING -> VERIFIED
PENDING -> EXPIRED
PENDING -> LOCKED
PENDING -> SUPERSEDED
```

## 6. Agent 执行路径

```text
自然语言请求
  -> 结构化意图与收款人解析
  -> 生成不可变转账预览
  -> Policy Engine 计算风险
  -> 查询同一收款人的活动冷静期
  -> 高危组合：创建或复用风险事件
  -> Run 进入 COOLING_OFF，后续节点停止
```

转账执行节点会再次查询活动冷静期。即使旧 Run 已经取得确认或 MFA，只要另一条请求在此期间触发了同一收款人的冷静期，旧授权仍不能完成扣款。

## 7. 受信 App 释放

接口均要求当前用户 JWT：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/fraud-cooling/events` | 当前用户风险事件列表 |
| `GET` | `/api/fraud-cooling/events/{event_id}` | 风险事实与安全帮助 |
| `GET` | `/api/fraud-cooling/events/{event_id}/audits` | 事件审计 |
| `POST` | `/api/fraud-cooling/events/{event_id}/appeals` | 提交一次误报申诉 |
| `POST` | `/api/fraud-cooling/events/{event_id}/release-challenges` | 在受信 App 创建 MFA 挑战 |
| `POST` | `/api/fraud-cooling/release-challenges/{challenge_id}/verify` | 验证并解除冷静期 |
| `GET` | `/api/demo/fraud-release-challenges/{challenge_id}/code` | 仅本地环境获取演示码 |

Channel Gateway 没有释放接口。飞书只返回 `OPEN_APP_FRAUD_REVIEW` 安全动作和受信 App 链接，不能携带验证码或替用户完成释放。

验证成功后的响应明确返回：

```json
{
  "funds_executed": false,
  "message": "冷静期已解除；原转账不会自动恢复，请重新发起并再次接受风控。"
}
```

## 8. 前端

当 Run 状态为 `COOLING_OFF` 时，对话工作区展示：

- 关联收款人的脱敏名称与账号。
- 北京时间冷静期结束时间、关联重试次数和策略版本。
- 每项确定性命中事实，不展示模型自由文本作为授权依据。
- 96110 安全求助入口和停止屏幕共享等处置建议。
- 误报申诉输入框。
- 受信 App MFA 释放流程和本地演示码。

桌面和移动端使用同一数据契约。冷静期结束后仍提示原转账没有自动执行。

## 9. 验收与测试

```powershell
python -m pytest backend/tests/test_fraud_cooling.py -q
python -m pytest backend/tests ai/evals -q
```

自动化覆盖：

- 改写措辞、降低金额和新会话仍阻断同一收款人。
- 其他收款人正常进入黄色或红色流程。
- 相同名称在不同用户之间完全隔离。
- 冷静期截止时刻立即按过期处理。
- 误报申诉不解除风险事件。
- 飞书释放被稳定错误码拒绝。
- MFA 三次错误锁定，正确验证只解除事件、不执行资金。
- 并发触发只产生一个活动事件。
- 旧 MFA 授权在执行前遇到新冷静期时停止扣款。

## 10. 已知边界

- 当前诈骗语义仍以确定性规则为主，可能存在误报和漏报，因此提供事实证据和申诉入口。
- 比赛环境使用模拟短信验证码；生产环境需接入设备绑定、短信/人脸/U 盾和设备风险。
- 当前冷静期只覆盖转账场景，生产环境可扩展到虚拟卡、贷款放款和高风险理财赎回。
- 本功能不代替警方、反诈中心或银行人工调查。

