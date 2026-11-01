# DeepSeek 与多轮对话配置

## 1. 技术选型结论

DeepSeek 结构化意图解析不引入 LangChain，直接使用 OpenAI 兼容 SDK 和 Pydantic Schema。项目使用 LangGraph 编排生活事件检查点，并使用独立的确定性 `TaskGraphExecutor` 执行账单、订阅、转账、跨场景分析和生活事件任务 DAG。

| 需求 | 当前选择 | 原因 |
| --- | --- | --- |
| DeepSeek 调用 | OpenAI Python SDK | DeepSeek 提供兼容接口，依赖少、调用链清晰 |
| 结构化输出 | Pydantic Schema | 严格限制字段、类型、金额范围和未知属性 |
| 多轮状态 | `AgentRuntime` + Session；生活事件使用 LangGraph | 与确认、OTP、Policy Engine 状态边界一致 |
| 超时和重试 | OpenAI SDK timeout + 1 次重试 | 避免现场演示长时间等待 |
| 模型降级 | 本地中文解析器 | 无密钥、超时、接口错误和格式错误时仍可演示 |

LangChain 当前不会减少核心代码，反而会增加抽象和排错成本。等项目需要大量异构工具、Prompt 模板管理或供应商切换时再评估。LangGraph 的当前范围和恢复方式见 [`LANGGRAPH_WORKFLOW.md`](./LANGGRAPH_WORKFLOW.md)；任务图执行契约见 [`TASK_GRAPH_CONTRACT.md`](./TASK_GRAPH_CONTRACT.md)。Policy Engine、确认和强验证始终保留在后端确定性边界内，DeepSeek 无权放行写操作。

## 2. 你需要填写的位置

在仓库根目录执行：

```powershell
Copy-Item .env.example .env
```

打开根目录的 `.env`，只填写这一行：

```dotenv
DEEPSEEK_API_KEY=你的DeepSeek_API_Key
```

完整配置已经预设为：

```dotenv
DEEPSEEK_ENABLED=true
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_TIMEOUT_SECONDS=10
VITE_API_BASE_URL=http://localhost:8000
```

`DEEPSEEK_API_KEY` 必须来自 DeepSeek 开放平台，不是 OpenAI Key。不要将真实密钥粘贴到聊天、截图、PPT 或 Git 仓库中。`.gitignore` 已忽略 `.env`。

## 3. 启动与验证

配置后重启后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

打开下面的接口：

```text
http://localhost:8000/api/agent/model-status
```

配置正确时应看到：

```json
{
  "provider": "deepseek",
  "enabled": true,
  "configured": true,
  "ready": true,
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-chat",
  "timeout_seconds": 10
}
```

前端右上角会显示“DeepSeek 已连接”。一次请求成功解析后，右侧“意图引擎”会显示 DeepSeek 返回的模型版本、状态和延迟。

## 4. 回退行为

| 模型状态 | 系统行为 |
| --- | --- |
| `DISABLED` | 未启用或未填写密钥，使用本地解析器 |
| `SUCCEEDED` | DeepSeek 输出通过 Pydantic 校验 |
| `TIMEOUT` | 超时后回退，不执行未经校验的模型输出 |
| `API_ERROR` | 网络、额度或服务错误后回退 |
| `INVALID_OUTPUT` | JSON 或 Schema 不合格，丢弃模型结果并回退 |

模型调用轨迹只记录提供商、请求模型、响应模型、延迟、状态、错误类型和请求编号，不记录 API Key。

## 5. 多轮对话行为

每个 `session_id` 保存：

- 历史 Run 编号
- 等待补充的结构化意图
- 缺失字段和 10 分钟有效期
- 上一次明确收款人
- 最近更新时间

示例一：

```text
用户：给小王转账
Agent：还需要补充金额。
用户：200 元
Agent：请核对向小王转账 200 元的预览。
```

示例二：

```text
用户：给女儿转 100 元
用户：转账 200 元
Agent：还需要补充收款人。
用户：就是刚才那个人
Agent：请核对向女儿转账 200 元的预览。
```

会话通过 `POST /api/agent/sessions` 由后端创建并绑定当前 JWT 用户。前端登录后调用 `GET /api/agent/sessions` 恢复最近会话，再通过 `/api/agent/sessions/{session_id}` 获取历史；浏览器不持久化会话 ID。相同 `Idempotency-Key` 的重复请求只返回第一次创建的 Run。
