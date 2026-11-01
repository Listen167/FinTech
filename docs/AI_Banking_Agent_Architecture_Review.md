# SafeBank Agent 架构与接口审阅记录

> **文档状态：历史审阅记录，不作为当前接口契约。**
>
> 当前接口以运行时 `/openapi.json` 为准；当前架构和运行方式见
> [`USER_MANUAL.md`](./USER_MANUAL.md) 与 [`IMPLEMENTATION_GUIDE.md`](./IMPLEMENTATION_GUIDE.md)。

日期：2026-09-16

## 1. 参考项目与借鉴点

本次审阅参考了以下公开项目或规范：

- Open Bank Project / OBP-API: https://github.com/OpenBankProject/OBP-API
- Open Bank Project / OBP-MCP: https://github.com/OpenBankProject/OBP-MCP
- Mastercard Open Banking US OpenAPI: https://github.com/Mastercard/open-banking-us-openapi
- Open Banking UK Read/Write API Specs: https://github.com/OpenBankingUK/read-write-api-specs
- Agent Service Toolkit: https://github.com/JoshuaC215/agent-service-toolkit

主要可借鉴点：

- 金融 API 不只需要业务路径，还需要请求追踪、幂等、防重放、统一错误码、分页和审计关联。
- Open Banking 项目通常把账户、交易、支付、授权、同意、审计等资源拆开，不把所有行为塞进一个聊天接口。
- AI Agent 接入银行 API 时，最好有单独的工具调用层或 MCP/Tool Gateway，避免 Agent 直接调用任意业务接口。
- 需要把“用户确认”和“强验证”作为明确状态机资源，而不是一次性字段。
- OpenAPI 应适合生成客户端，因此每个 operation 需要稳定的 `operationId`、统一响应模型和可复用组件。

## 2. 当前架构评价

当前技术文档的方向是正确的，特别是以下设计值得保留：

- 前端、后端、AI、Policy Engine、Mock Bank Sandbox 的边界清楚。
- 绿色、黄色、红色权限分级符合银行业务风险分层。
- 文档明确要求 LLM 不能直接修改账户状态，后端必须二次校验。
- 覆盖账单分析、智能转账、订阅管理三条闭环，适合比赛演示。
- 安全测试包含 Prompt 注入、参数篡改、重放攻击和验证码失败熔断。

需要加强的地方：

- 缺少“Agent Runtime”概念。复杂任务需要 `run_id`、状态、待处理动作、失败恢复和重试边界。
- 缺少“Consent / 授权范围”的轻量设计。即使是 Mock 银行，也应表达用户授权查询账户、读取流水、发起转账预览等权限范围。
- 接口层缺少金融 API 常见的跨切面字段：`X-Request-Id`、`Idempotency-Key`、统一错误、分页游标、审计 ID。
- 当前 `/api/agent/confirm` 直接执行黄色操作，语义过重；建议改成 confirmation 资源的确认/拒绝动作。
- 当前验证码接口只表达 OTP，不表达验证会话、场景、绑定操作和验证令牌有效期。
- 当前 OpenAPI 未定义 `operationId`，后续生成 TypeScript/Python 客户端时会不稳定。
- AI 工具白名单在文档中有，但接口未体现“内部工具入口”和普通前端 API 的隔离。

## 3. 是否需要重新定义接口

结论：需要做 v0.2 的接口重定义，但不需要推翻业务资源。

应保留：

- `accounts`
- `transactions`
- `bills`
- `contacts`
- `transfers`
- `subscriptions`
- `cards`
- `security`
- `audit-logs`

应重定义：

- Agent 主入口从“单次 chat 请求”升级为“Agent Run”资源。
- 确认动作从 `/agent/confirm` 升级为 `/agent/confirmations/{confirmation_id}/decide`。
- 强验证从简单 OTP 接口升级为 `verifications` 资源。
- 转账执行接口从 `/transfers/execute` 调整为 `/transfers/{preview_id}/execute`，并强制幂等键。
- 所有写操作必须支持 `Idempotency-Key`。
- 所有接口必须支持 `X-Request-Id` 并在响应和审计日志中关联。
- 查询列表统一分页模型。
- 新增内部工具入口 `/internal/v1/tools/{tool_name}/invoke`，供 AI 工具路由使用，和前端公开 API 分离。

## 4. 建议的 v0.2 接口原则

### 4.1 公开 API 与内部工具 API 分离

公开 API 面向前端：

- 登录、当前用户
- Agent Run 创建和查询
- 确认、强验证
- 账户、交易、账单、订阅、审计展示

内部 API 面向 AI 服务：

- 工具白名单调用
- Tool Schema 校验
- 后端 Policy Engine 再校验
- 工具调用审计

### 4.2 写操作必须幂等

以下接口必须要求 `Idempotency-Key`：

- 创建 Agent Run
- 确认黄色操作
- 发起强验证
- 校验验证码
- 创建转账预览
- 执行转账
- 取消订阅

### 4.3 统一错误模型

错误响应至少包含：

- `code`
- `message`
- `request_id`
- `audit_id`
- `details`

建议错误码：

- `VALIDATION_ERROR`
- `AUTH_REQUIRED`
- `FORBIDDEN`
- `CONFIRMATION_REQUIRED`
- `STRONG_VERIFICATION_REQUIRED`
- `IDEMPOTENCY_CONFLICT`
- `REPLAY_DETECTED`
- `POLICY_BLOCKED`
- `RATE_LIMITED`
- `TOOL_EXECUTION_FAILED`
- `INTERNAL_ERROR`

### 4.4 Agent Run 状态机

推荐状态：

- `RECEIVED`
- `PLANNING`
- `WAITING_CONFIRMATION`
- `WAITING_VERIFICATION`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`
- `ABORTED`

Agent 回复中应包含：

- `run_id`
- `session_id`
- `state`
- `message`
- `intent`
- `plan`
- `policy`
- `pending_action`
- `data`
- `audit_id`

### 4.5 确认与强验证必须绑定原始操作快照

确认和验证令牌不能只靠前端提交金额、收款人等字段。后端必须保存原始操作快照，并在执行时校验：

- `preview_id`
- `confirmation_id`
- `verification_id`
- `operation_type`
- `operation_snapshot_hash`
- `expires_at`
- `used_at`
- `audit_id`

## 5. 对技术文档的修改建议

建议在原技术文档中补充：

- `Agent Runtime / Run State` 小节。
- `Consent / 授权范围` 小节。
- `接口治理规范` 小节。
- `公开 API 和内部工具 API 分层` 小节。
- `幂等、防重放、统一错误和分页` 小节。
- 数据库设计新增 confirmations、verifications、agent_runs、tool_invocations。

## 6. 产物

本次已新增 v0.2 接口草案：

- `interfaces/openapi/safebank-agent.v0.2.openapi.yaml`

现有 v0.1 文件保留：

- `docs/AI_Banking_Agent_OpenAPI.yaml`
- `interfaces/openapi/safebank-agent.openapi.yaml`

