# 功能与实现指南

## 1. 已实现功能

### 1.1 输入安全

`ai/safety/guard.py` 在调用 LLM 前进行 Unicode 规范化和确定性匹配。规则分为两类：

- 硬拦截：覆盖系统提示、跳过确认、绕过验证码。
- 风险线索：安全账户、冒充客服、紧迫、保密和远程控制。

硬拦截不会进入规划；风险线索进入 Policy Engine，只能提高风险等级。

### 1.2 意图理解

`ai/agent/intent.py` 提供稳定的中文离线解析，支持账单、订阅、转账和跨场景表达。`ai/agent/llm_parser.py` 使用 OpenAI Python SDK 调用 DeepSeek 兼容接口，温度固定为 0，并要求 JSON 对象输出。返回值必须通过 `StructuredIntent` Pydantic Schema；模型不可用、超时或格式错误时自动回退，不影响现场演示。

生产增强应继续增加置信度校准、长期上下文治理和更大规模离线评测集；任何情况下都不要直接执行模型生成的函数名称。

### 1.2.1 多轮槽位补充

后端按 `session_id` 保存等待补充的意图、缺失字段、上一收款人和历史 Run。会话 ID 由后端使用高熵 UUID 创建并绑定 JWT 当前用户，客户端提交未知会话 ID 不会触发隐式创建。缺失信息时状态进入 `WAITING_INPUT`，有效期为 10 分钟；后续短回答只补充已声明的字段，不能改变权限。完成补充后，旧 Run 标记为 `RESOLVED`，新 Run 从完整意图生成转账预览。

前端登录后从服务端获取当前用户的会话列表并恢复最近会话，也可以显式新建或切换会话，因此刷新、后端重启和更换设备后都能恢复 MySQL 中的历史。相同幂等键的重试不会创建重复 Run。

### 1.2.2 MySQL 持久化

项目通过 SQLAlchemy 2.0 和 PyMySQL 连接 MySQL，使用 Alembic 管理表结构。`agent_sessions`、`agent_runs`、`confirmations`、`verifications`、`idempotency_keys` 和 `audit_logs` 保存 Agent 状态；银行沙箱账户、流水、订阅和转账预览也会同步持久化，因此后端重启后可以继续补槽或完成待授权操作。

完整状态快照存入 MySQL JSON 字段，状态、会话 ID、操作类型和时间等常用检索字段单独建列与索引。比赛版保持单进程执行；生产环境需要把当前快照同步进一步拆成行级事务和乐观锁。

首次配置后执行：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`GET /api/health` 会返回当前仓库类型和连接状态。测试通过根目录 `conftest.py` 强制使用内存仓库，不读取或污染开发数据库。

### 1.2.3 用户身份基础

第二个 Alembic 迁移建立 `users`、`password_credentials`、`auth_sessions` 和 `auth_events`。当前写入两个完全虚构的用户，用于后续验证多用户隔离：

| 用户 ID | 显示名称 | 角色 | 状态 |
| --- | --- | --- | --- |
| `user_001` | 林安 | CUSTOMER | ACTIVE |
| `user_002` | 周宁 | CUSTOMER | ACTIVE |

用户手机号只保存掩码。演示密码仅以 Argon2id 哈希写入数据库，不开放未鉴权的用户查询接口；登录后由短期 JWT 和可撤销刷新会话维持身份。

### 1.2.4 多用户资源隔离

第三个 Alembic 迁移为账户、联系人、订阅、交易、Agent Session、Run、确认、验证、转账预览、幂等键和审计增加 `user_id`、索引与外键。所有银行沙箱查询和写操作都按内部用户 ID 过滤，确认和强验证同时绑定用户与操作快照。

Agent Runtime 不再设置默认用户。除健康检查、认证状态和模型状态外，Agent、Session、确认、验证、审计与演示重置接口都必须从服务端验证过的 JWT 认证上下文取得用户 ID，不接受请求体传入身份。测试证明 `user_002` 不能读取 `user_001` 的 Run、Session 或授权对象。同一个客户端幂等键在不同用户作用域内互不冲突。

### 1.2.5 密码登录与短期 JWT

密码通过 `pwdlib` 的推荐 Argon2id 参数生成不可逆哈希。登录服务在数据库事务中锁定凭据行，连续失败 5 次后锁定 10 分钟，并对成功、失败、锁定、Token 拒绝和注销进行认证审计。未知用户名仍执行一次虚拟 Argon2 校验，减少用户名枚举的时序差异。

Access Token 默认有效期 10 分钟，并同时校验算法、签发方、受众、令牌类型、服务端登录会话和凭据版本。JWT 不承担资金授权；黄色确认、红色强验证和操作快照仍由 Policy Engine 与 Agent Runtime 控制。配置及初始化步骤见 `docs/AUTH_SETUP.md`。

Refresh Token 是高熵随机值，只通过 HttpOnly Cookie 传输，数据库在 `auth_refresh_tokens` 中保存 SHA-256 哈希和轮换历史。刷新接口要求双提交 CSRF Token；成功后同时轮换两个 Token。检测到已轮换 Refresh Token 被再次使用时，系统撤销整个登录会话并写入阻断审计。Access Token 注销检查依赖服务端 `auth_sessions`，因此不需要等待 JWT 自然过期。

### 1.2.6 前端认证与自动续期

前端登录成功后只在 `frontend/src/api/client.ts` 的模块内存中保存 Access Token，刷新页面会先读取可见的 CSRF Cookie，再携带浏览器自动管理的 HttpOnly Refresh Cookie 调用刷新接口。多个并发 401 共用同一个刷新请求，刷新成功后原请求最多重试一次；刷新失败或重试后仍为 401 时立即清理工作台并返回登录页。

浏览器持久化存储不保存 Agent `session_id`、Access Token、Refresh Token、密码或完整账户信息。Agent 会话由后端生成并持久化到 MySQL；退出登录与全部设备下线均由服务端撤销认证会话，前端仅在服务端操作成功后清理当前用户状态。

### 1.3 DAG 规划

`ai/agent/planner.py` 为每类意图生成有标签的有向无环任务。每个写操作都包含“预览 → 策略 → 人工授权 → 执行”，前端可直接展示节点。

新计划遵循 [`TASK_GRAPH_CONTRACT.md`](./TASK_GRAPH_CONTRACT.md)，包含 `schema_version=1`、初始 `execution_mode=DECLARATIVE` 和节点 `runtime` 状态。`TaskGraphExecutor` 重新校验后把计划切换为 `EXECUTABLE`，并只调用构造时显式注册的后端处理器，不执行模型生成代码。

账单、订阅、转账、跨场景分析和生活事件资金规划均由该执行器推进。确认、MFA 和生活图谱确认对应 `WAITING` 授权节点；授权通过后才恢复正式写操作。每个节点保存规范化输入 SHA-256、尝试次数、开始/结束时间、稳定错误码和最小输出回执。回执不复制完整工具响应；状态转换缺少证据时返回统一中文错误并停止自动推进。

### 1.4 动态权限

`backend/app/services/policy_engine.py` 使用确定性规则：

| 条件 | 分值/策略 |
| --- | --- |
| 只读查询 | 绿色，10 分 |
| 取消订阅 | 黄色，45 分 |
| 任意转账 | 基础 35 分 |
| 金额大于 1000 元 | 加 40 分 |
| 新收款人 | 加 15 分 |
| 每项诈骗语义 | 加 18 分，最多 45 分 |
| 70 分及以上 | 红色强验证 |
| 95 分及以上 | 阻断 |

生产环境应将阈值配置化、版本化，并经过风控团队审批。

### 1.5 确认、强验证与防篡改

Agent Runtime 先保存不可变操作对象，再对规范化 JSON 计算 SHA-256。确认或 OTP 通过后重新计算；摘要不一致则阻断。确认对象使用后变为 `USED`，再次提交返回 `REPLAY_DETECTED`。

红色操作生成随机六位 OTP，验证码使用独立 Pepper 对 `verification_id + code` 计算 HMAC-SHA256，数据库只保存摘要。Verification 同时绑定 JWT 用户、Run、操作快照、签发时间和五分钟有效期；连续失败三次进入 `LOCKED`，成功、过期、取消或熔断后立即销毁投递箱中的验证码。重发操作设置冷却时间并生成新摘要，使旧验证码立即失效。

本地环境提供需要登录且校验资源所有权的模拟短信取码接口，响应带 `Cache-Control: no-store`；非本地环境返回 404。生产环境应将模拟投递箱替换为银行短信、设备确认、人脸或 U 盾适配器。

### 1.6 幻觉防护

业务成功只能来自 `BankService` 返回值。提示词明确禁止模型编造，Runtime 也不会根据模型文本修改账户。前端展示银行沙箱生成的 `transaction_id` 和后端生成的 `audit_id`。

## 2. API 主流程

除 `GET /api/health`、认证状态/登录接口和 `GET /api/agent/model-status` 外，下列业务接口均需要在 `Authorization: Bearer <Access Token>` 中携带短期 Access Token。前端不得自行提交或切换 `user_id`。

```text
POST /api/agent/runs
POST /api/agent/sessions
GET  /api/agent/sessions
GET  /api/agent/runs/{run_id}
GET  /api/agent/sessions/{session_id}
GET  /api/agent/model-status
POST /api/agent/confirmations/{confirmation_id}/decide
POST /api/agent/verifications/{verification_id}/verify
POST /api/agent/verifications/{verification_id}/resend
POST /api/agent/runs/{run_id}/abort
GET  /api/audit-logs?run_id=...
POST /api/demo/reset                         # 仅本地环境且需要登录
GET  /api/demo/verifications/{id}/code       # 仅本地演示且需要资源所有权
```

运行服务后，以 FastAPI 的 `/openapi.json` 和 `/docs` 为当前实现的权威接口说明。执行 `python -m scripts.export_openapi` 可生成 `docs/AI_Banking_Agent_OpenAPI.json` 并导入 Apifox；`docs/AI_Banking_Agent_OpenAPI.yaml` 和 `interfaces/openapi` 中的文件均为历史手工草案。

## 3. 三条完整调用路径

### 账单分析

创建 Run → 安全检测 → `bill.analyze` 意图 → 绿色策略 → 读取流水 → 分类与异常识别 → 审计 → 返回结果。

### 取消订阅

创建 Run → 定位订阅 → 黄色策略 → 创建确认与快照 → 用户确认 → 校验快照与单次状态 → 修改沙箱订阅 → 审计。

### 转账

创建 Run → 提取收款人与金额 → 生成预览 → 动态策略 → 黄色确认或红色 OTP → 快照校验 → 检查余额 → 扣减沙箱余额 → 返回交易编号与审计编号。

## 4. 接入真实 LLM

复制 `.env.example` 为 `.env`，只需填写 `DEEPSEEK_API_KEY`。不要提交 `.env`。当前预设 `https://api.deepseek.com` 和 `deepseek-chat`；详细验证方式见 `docs/DEEPSEEK_SETUP.md`。

答辩时应明确：当前 LLM 只参与结构化理解，资金授权由后端规则控制。这样既体现 AI，也能解释为何模型幻觉不会直接形成资金损失。

## 5. 生产化待办

- 将比赛版 JSON 快照同步升级为行级事务、乐观锁与数据库连接故障告警。
- 接入银行身份、真实 MFA、密钥管理、脱敏和细粒度 Consent Scope。
- 使用消息队列支持长任务、重试与补偿。
- 为规则版本、模型版本、Prompt 版本建立审计关联。
- 建立至少 300 条中文金融意图与攻击评测集。
- 完成等保、隐私影响评估、模型风险治理与灾备演练。
