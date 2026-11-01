# AI 能力层

负责输入安全、结构化意图识别、任务 DAG 和工具白名单，不直接修改银行业务状态。

当前流程：

```text
用户消息 -> 安全检测 -> LLM/本地意图解析 -> DAG Planner -> 后端 Policy Engine -> 白名单工具
```

Planner 生成的计划使用 [`task_graph.py`](./agent/task_graph.py) 定义的版本化节点契约。当前执行模式为 `DECLARATIVE`，表示计划用于解释和展示；普通银行业务仍由后端确定性 Runtime 路由，不能把该计划描述为通用 DAG Executor 的执行结果。

复制根目录 `.env.example` 为 `.env`，填写 `DEEPSEEK_API_KEY` 后可启用 DeepSeek。系统使用 OpenAI Python SDK 调用兼容接口，并用 Pydantic 校验结构化输出；未配置、超时或校验失败时自动回退到确定性中文解析器。

