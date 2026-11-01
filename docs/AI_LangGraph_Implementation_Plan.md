# SafeBank AI 服务层 LangGraph 落地方案（历史草案）

> **文档状态：仅供历史决策追溯，请勿作为当前实现依据。**
>
> LangGraph 编排现已落地到后端运行时，正式实现、状态恢复、安全边界和测试说明请以
> [`LANGGRAPH_WORKFLOW.md`](./LANGGRAPH_WORKFLOW.md) 为准；整体进度请以
> [`DEVELOPMENT_CHECKLIST.md`](./DEVELOPMENT_CHECKLIST.md) 为准。本文中的独立 AI 服务、
> `SqliteSaver`、LangChain 工具执行等内容均未被当前方案采用。

版本：v0.1
日期：2026-09-16
模型选型：DeepSeek
状态：方案草案（仅评审用，未改代码）

## 1. 目标

把当前 `ai/` 目录下的骨架（`guard` / `planner` / `registry`）升级为可运行的 Agent 编排层，引入 LangChain + LangGraph 承载以下能力，同时保持项目文档要求的「LLM 无权绕过权限」这一安全底线：

- 自然语言意图解析（结构化输出）。
- 任务规划（DAG）。
- 白名单工具调用（走内部 Tool Gateway）。
- 多轮确认 / 强验证（人机中断）。
- 完整审计链路与 Agent Run 状态机。

本方案不修改 `backend`，只规划 `ai/` 侧；落地时后端需按 v0.2 接口补齐 `/api/agent/runs` 与 `/internal/v1/tools/{tool_name}/invoke`。

## 2. 现状盘点

| 现有文件 | 作用 | 方案中的处置 |
| --- | --- | --- |
| `ai/agent/orchestrator.py` | 编排入口，占位 | 改为 LangGraph 图的入口 |
| `ai/agent/planner.py` | 占位 DAG | 保留，接 LLM 计划 + 规则校验 |
| `ai/safety/guard.py` | 注入关键词检测 | **保留为规则节点**，不进 LLM |
| `ai/tools/registry.py` | 工具白名单 | **保留为白名单权威**，LangChain Tool 只暴露其中工具 |
| `ai/prompts/system.md` | 系统提示词 | 保留，作为 LangChain 提示词模板内容 |
| `ai/evals/` | 评测集目录 | 后续补充意图 / 注入 / 转账样例 |

设计原则：**规则归规则，模型归模型**。`guard.py` 与 `registry.py` 是确定性规则，不交给 LLM 决定；LLM 只负责理解与规划，最终放行权在规则引擎与后端 Policy Engine。

## 3. 技术选型与依赖

新增 Python 依赖（建议在 `ai/` 下独立 `requirements.txt`，与后端隔离）：

```text
langchain>=0.3.0
langchain-core>=0.3.0
langgraph>=0.2.0
langchain-deepseek>=0.1.0
pydantic>=2.0.0
httpx>=0.27.0
```

说明：

- 用 `langchain-deepseek` 官方集成（`ChatDeepSeek`）；DeepSeek 同时兼容 OpenAI 协议，也可用 `langchain-openai` 的 `ChatOpenAI` 指向 `https://api.deepseek.com`。
- `httpx` 用于 AI 服务异步调用后端内部工具接口与 `/api/agent/runs`。
- 若后端仍为同步 FastAPI，AI 服务可先用 `requests` / `httpx` 同步调用，避免过早引入异步复杂度。

## 4. 目录结构规划（AI 侧新增）

```text
ai/
├── agent/
│   ├── graph.py          # LangGraph StateGraph 定义与节点编排
│   ├── state.py          # AgentRunState（Pydantic TypedDict 状态）
│   ├── nodes.py          # 各节点实现：guard/intent/plan/tool/policy/format
│   ├── orchestrator.py   # 对外入口：create_run / resume_run
│   └── planner.py        # 已有，DAG 生成与校验
├── intent/
│   └── schemas.py        # Intent 结构化输出 Pydantic 模型
├── tools/
│   ├── registry.py       # 已有白名单，保留
│   ├── gateway.py        # 后端内部工具调用客户端（httpx）
│   └── schemas.py        # 每个工具的 JSON Schema
├── safety/
│   └── guard.py          # 已有注入检测，保留
├── prompts/
│   ├── system.md         # 已有系统提示词
│   └── templates.py      # LangChain PromptTemplate 封装
├── llm.py                # ChatDeepSeek 初始化
├── requirements.txt      # AI 侧依赖
└── config.py             # DeepSeek API Key、后端 base_url 等
```

## 5. 核心状态设计

用 `StateGraph` 的 `State` 承载一次 Agent Run 的全部上下文，字段对齐 v0.2 的 `AgentRun` schema（`safebank-agent.v0.2.openapi.yaml:672`）：

```python
class AgentRunState(TypedDict):
    run_id: str
    session_id: str
    user_id: str
    message: str                    # 用户原始输入
    state: str                      # RECEIVED/PLANNING/... 状态机
    intent: dict | None             # 意图解析结果
    plan: dict | None               # 任务 DAG
    policy: dict | None             # 权限判断
    pending_action: dict | None     # 待确认 / 待验证动作
    tool_results: dict              # tool_name -> 结果快照
    reply: str                      # 最终返回给前端的文本
    audit_id: str | None
```

状态机取值沿用 v0.2：

```text
RECEIVED -> PLANNING -> RUNNING -> SUCCEEDED | FAILED
                       -> WAITING_CONFIRMATION
                       -> WAITING_VERIFICATION
                       -> BLOCKED / ABORTED
```

## 6. 图与节点设计

主链路（每个节点对应一个函数）：

```text
[guard] ──block──> BLOCKED(END)
   │ safe
   ▼
[intent] ──low_conf/missing──> 追问(END)
   │ ok
   ▼
[plan] ──> [tool] ──> [policy] ──┬─GREEN──> [execute] ──> [format] ──> SUCCEEDED
                                  ├─YELLOW──> [await_confirm] interrupt ──> WAITING_CONFIRMATION
                                  └─RED─────> [await_verify] interrupt ──> WAITING_VERIFICATION
```

节点职责：

| 节点 | 职责 | 是否调用 LLM |
| --- | --- | --- |
| `guard` | 调 `safety/guard.py` 检测注入；命中则转 BLOCKED | 否（规则） |
| `intent` | LLM 结构化输出意图，置信度 < 阈值或缺槽位则追问 | 是 |
| `plan` | 生成任务 DAG，并用白名单校验节点里所有 `tool` | 是（生成）+ 否（校验） |
| `tool` | 按 DAG 顺序调用后端内部工具接口 | 否 |
| `policy` | 调用后端 Policy 判断（或本地 `policy_engine` 参考值），拿到 GREEN/YELLOW/RED | 否（以后端为准） |
| `execute` | 绿色操作执行 / 黄色红色操作在确认后执行 | 否（走后端） |
| `await_confirm` / `await_verify` | `interrupt()` 挂起，返回确认 / 验证卡片 | 否 |
| `format` | LLM 把工具结果解释成用户可读文本（引用 `audit_id`） | 是 |

## 7. 意图解析（结构化输出）

对应技术文档 7.1。用 LangChain `with_structured_output` 让模型直接产出 Pydantic：

```python
from pydantic import BaseModel, Field

class Intent(BaseModel):
    name: str = Field(description="如 transfer.create / bill.analyze")
    confidence: float = Field(ge=0.0, le=1.0)
    slots: dict = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    risk_hints: list[str] = Field(default_factory=list)

structured_llm = llm.with_structured_output(Intent)
intent = structured_llm.invoke([
    SystemMessage(content=SYSTEM_PROMPT),
    HumanMessage(content=message),
])
```

规则兜底（不进 LLM）：

- `confidence < 0.7` → 转追问。
- `missing_slots` 非空 → 转追问。
- 含注入词（`guard` 已拦）→ 已 BLOCKED。

## 8. 工具网关（白名单 -> Tool）

`tools/registry.py` 的 `ALLOWED_TOOLS` 是权威白名单。为其中每个工具生成 LangChain `Tool`，`func` 统一走 `tools/gateway.py` 调后端：

```text
AI 工具调用 -> gateway.py -> POST /internal/v1/tools/{tool_name}/invoke
             { session_id, run_id, input }
```

关键点：

- 工具名必须落在 `ALLOWED_TOOLS`，否则网关拒绝（`registry.is_allowed_tool`）。
- 每个工具带 JSON Schema，参数由后端二次校验（v0.2 内部接口已声明 `enum` 白名单）。
- 工具结果只做事实来源，AI 不得编造账户 / 余额 / 交易号（对应文档 8.4）。

## 9. 权限分级与确认 / 强验证

权限判断最终以后端 `Policy Engine` 为准，AI 侧只做展示与中断。推荐**方案 A（后端权威执行，更简单更安全）**：

1. AI 调 `transfer.preview`，后端返回 `policy` + `confirmation`（黄）或触发强验证（红）。
2. 黄色：AI 到 `await_confirm` 节点 `interrupt()`，把 `confirmation_id` + 确认卡片返回前端，run 置 `WAITING_CONFIRMATION`。
3. 红色：AI 到 `await_verify` 节点 `interrupt()`，触发后端 `security/verifications`，run 置 `WAITING_VERIFICATION`。
4. **用户在前端点确认 / 输验证码后，由前端直接调后端** `/api/agent/confirmations/{id}/decide` 或 `/api/security/verifications/{id}/verify`，后端完成执行并二次校验快照。
5. 后端推进 run 状态到 `SUCCEEDED`，前端刷新即可。AI 无需在确认后再次恢复执行，避免重复执行风险。

LangGraph 的 `interrupt` 在这里的定位：**负责把「需要人等一步」这件事表达成可持久化的挂起点**，并把控制权交还给前端。是否用 `resume` 恢复执行，取决于团队选择。

- 若选**方案 B（AI 恢复执行）**：前端确认后带 `confirmation_id` 调 `resume_run`，LangGraph 从 `await_confirm` 节点恢复，调 `transfer.execute` 工具（带 `confirmation_id`），后端校验快照后执行。此方案更完整展示 LangGraph 能力，但要多维护恢复路径。

初赛建议先用方案 A，跑通后再补方案 B 用于演示「人机协同恢复」。

## 10. 安全规则层（保持独立）

以下规则**永远不进 LLM 决策链**：

1. `safety/guard.py`：输入注入检测，命中即 BLOCKED（文档 8.3）。
2. `tools/registry.py`：工具白名单，越权工具名直接拒绝（文档 7.3）。
3. 后端 `policy_engine.py`：GREEN/YELLOW/RED 分级，LLM 只能展示、不能改（文档 8.1）。
4. 幻觉防护：结果只来自工具返回，AI 生成文本必须带 `audit_id` 或 `source_tool`（文档 8.4）。

## 11. DeepSeek 配置

```python
# ai/llm.py
from langchain_deepseek import ChatDeepSeek

llm = ChatDeepSeek(
    model="deepseek-chat",        # 或 deepseek-reasoner 用于复杂规划
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0,                # 银行场景尽量确定性输出
    timeout=30,
)
```

环境变量（`.env`，不提交仓库）：

```text
DEEPSEEK_API_KEY=sk-xxx
BACKEND_BASE_URL=http://localhost:8000
```

结构化输出建议用 `deepseek-chat`；任务规划若需要更强推理可切 `deepseek-reasoner`。

## 12. 落地里程碑

| 阶段 | 内容 | 依赖 |
| --- | --- | --- |
| M1 | 接入 `ChatDeepSeek`，跑通 `intent` 结构化输出 | DeepSeek Key |
| M2 | 搭 `StateGraph` 骨架，guard → intent → 追问闭环 | 无 |
| M3 | 实现 `gateway.py` 与工具 JSON Schema，接通后端内部工具接口 | 后端 v0.2 内部接口 |
| M4 | 接入 `policy` 分支 + `interrupt` 确认 / 强验证 | 后端 confirmation/verification 接口 |
| M5 | 接 `format` 输出解释文本 + 审计 ID 关联 | 后端 audit 接口 |
| M6 | 补 `evals/` 评测集，回归注入 / 越权 / 幻觉用例 | 无 |

## 13. 待拍板的关键决策

1. **执行模型**：方案 A（后端权威执行，前端直接 decide）还是方案 B（LangGraph resume 恢复执行）？初赛建议 A。
2. **规划模型**：任务 DAG 用 LLM 生成 + 白名单校验，还是先用规则模板（转账 / 账单 / 订阅三条固定链路）+ LLM 只填槽位？初赛建议后者，更稳。
3. **异步**：AI 服务走异步（`httpx.AsyncClient` + `async` 节点）还是同步？取决于后端是否异步。
4. **对话记忆**：多轮上下文是否用 LangGraph `checkpointer`（如 `SqliteSaver` / `MemorySaver`）持久化？建议用 `SqliteSaver` 便于审计回溯。
