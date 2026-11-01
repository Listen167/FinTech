# 任务图运行契约

## 1. 当前范围

本契约对应增强清单 B1-B8，定义版本化节点结构、状态转换、最小执行证据和确定性调度边界。AI 规划层新生成的计划先标记为：

```json
{
  "schema_version": 1,
  "execution_mode": "DECLARATIVE"
}
```

`DECLARATIVE` 表示计划尚未执行，不能据此声称业务成功。`TaskGraphExecutor` 重新校验计划并接管后，将其标记为 `EXECUTABLE`；只有后端注册处理器取得可信回执，节点才能进入 `SUCCEEDED`。

## 2. 节点结构

```json
{
  "id": "n1",
  "type": "TOOL",
  "label": "读取本月交易",
  "tool": "transaction.list",
  "input": {},
  "depends_on": [],
  "runtime": {
    "status": "PENDING",
    "revision": 0,
    "attempt": 0,
    "input_digest": "输入规范化 JSON 的 SHA-256"
  }
}
```

节点类型包括 `VALIDATION`、`TOOL`、`POLICY`、`HUMAN_CONFIRM` 和 `RESPONSE`。`depends_on` 和 `tool` 保留计划依赖与工具名称；所有新计划在返回前必须通过 B3 结构与安全校验。

## 3. 最小执行证据

| 字段 | 说明 |
| --- | --- |
| `attempt` | 当前执行尝试次数，每次进入 `RUNNING` 时递增 |
| `input_digest` | 对排序键、无多余空白的 UTF-8 JSON 计算 SHA-256 |
| `started_at` | 最近一次进入 `RUNNING` 的带时区 UTC 时间 |
| `finished_at` | 进入 `SUCCEEDED`、`FAILED` 或 `SKIPPED` 的时间 |
| `error_code` | 失败时的稳定错误码，不保存堆栈和内部异常文本 |
| `output_receipt` | 输出类型、摘要、引用编号和输出 SHA-256，不保存完整工具结果 |

成功节点必须有可信输出回执。例如银行工具返回完整结果后，任务图只保存：

```json
{
  "kind": "TOOL_RESULT",
  "output_digest": "64位SHA-256",
  "summary": "银行沙箱返回交易回执",
  "reference_id": "txn_demo_001"
}
```

账户、验证码、Token 和完整交易结果不能复制到回执中。用户界面可以把 UTC 时间转换为北京时间，但持久化证据统一使用带时区时间。

## 4. 状态含义

| 状态 | 含义 |
| --- | --- |
| `PENDING` | 计划已生成，依赖尚未由执行器判定完成 |
| `READY` | 依赖满足，可以调度 |
| `RUNNING` | 后端确定性执行器正在处理 |
| `WAITING` | 等待确认、MFA、补槽或人工接管 |
| `SUCCEEDED` | 节点取得可信完成结果 |
| `FAILED` | 节点失败并停止自动推进 |
| `SKIPPED` | 因分支、拒绝或上游结果而明确跳过 |

`WAITING`、`FAILED` 和 `SKIPPED` 必须提供原因。每次合法转换都会递增 `revision`，后续持久化时可用于检测陈旧更新。

## 5. 合法转换

```text
PENDING -> READY -> RUNNING -> SUCCEEDED
    |          |         |
    |          |         +-> FAILED
    |          |         +-> READY（仅用于中断恢复）
    |          |         +-> WAITING -> READY/RUNNING
    |          |                         |-> FAILED
    +----------+-------------------------+-> SKIPPED
```

`SUCCEEDED`、`FAILED` 和 `SKIPPED` 是终态。禁止 `PENDING -> SUCCEEDED` 等越级转换，也禁止把终态直接改回可执行状态。后端重启时，只有持久化为 `RUNNING` 的非终态节点可回到 `READY`；写操作恢复前还必须重新核验授权和业务事实。

## 6. 结构与权限校验

`validate_task_graph` 在计划返回和每次进入执行器前检查：

- 节点 ID 唯一，依赖不能重复、缺失或指向自身。
- 使用拓扑排序检测循环依赖，不能依赖节点在列表中的排列顺序。
- `TOOL` 和 `POLICY` 必须声明白名单工具，其他节点不能伪装调用工具。
- `transfer.execute`、`subscription.cancel.execute` 和 `life_graph.change_request.decide` 属于受保护写操作。
- 每个受保护写操作的祖先路径必须同时包含 `POLICY` 和 `HUMAN_CONFIRM`，不能通过调整列表顺序绕过。

候选创建和预览可以在授权前生成，因为它们不改变正式业务事实；正式转账、取消订阅和生活图谱写入必须经过策略与人工授权。

## 7. 执行与恢复

`backend/app/services/task_graph_executor.py` 不读取模型输出中的代码，也不按任意字符串导入函数。节点只能命中构造执行器时注册的处理器；未注册处理器以 `TASK_GRAPH_HANDLER_NOT_REGISTERED` 失败并停止下游推进。

- 依赖全部成功后，节点才从 `PENDING` 进入 `READY`。
- 每次状态变化后立即把任务图写回 Run；MySQL 重启后从该检查点恢复。
- `WAITING` 不会自动放行。黄色确认、红色 MFA 和生活图谱确认必须提供与节点、Run、用户及操作快照绑定的授权证据。
- 写工具执行前再次检查当前资源快照；转账回执或生活图谱业务事实已存在时只做一致性恢复，不重复写入。
- 用户拒绝、主动中断或人工接管会把等待中和未执行节点标记为 `SKIPPED`，并撤销未使用授权。
- 生活图谱恢复重新读取 MySQL 候选请求状态，不信任 LangGraph 检查点替代业务事实。

## 8. 错误提示

| 错误码 | 用户提示原则 |
| --- | --- |
| `INVALID_TASK_NODE_TRANSITION` | 当前状态不允许推进，停止自动执行 |
| `TASK_NODE_INPUT_TAMPERED` | 输入与已保存摘要不一致，不透露具体内部字段 |
| `TASK_NODE_RECEIPT_REQUIRED` | 没有可信回执，不能声明成功 |
| `TASK_NODE_ERROR_CODE_REQUIRED` | 失败证据不完整，停止自动推进 |
| `TASK_NODE_TIME_INVALID` | 时间证据缺失、无时区或先后顺序错误 |
| `TASK_GRAPH_CYCLE_DETECTED` | 计划存在环，拒绝进入执行阶段 |
| `TASK_GRAPH_TOOL_NOT_ALLOWED` | 计划引用未知或未授权工具 |
| `TASK_GRAPH_WRITE_POLICY_REQUIRED` | 受保护写操作缺少前置策略节点 |
| `TASK_GRAPH_WRITE_AUTHORIZATION_REQUIRED` | 受保护写操作缺少前置人工授权节点 |
| `TASK_GRAPH_HANDLER_NOT_REGISTERED` | 节点没有已注册的后端处理器 |
| `TASK_GRAPH_NODE_EXECUTION_FAILED` | 处理器异常，未声明业务成功 |
| `TASK_GRAPH_RECOVERY_AUTHORIZATION_MISSING` | 恢复缺少可信授权依据，停止自动推进 |

这些错误在统一应用错误目录中使用中文稳定提示。节点只保存错误码，不保存数据库、SDK、模型密钥或堆栈信息。

## 9. 安全边界

- 节点状态不能替代 Policy Engine、用户确认或 MFA。
- LLM 不能自行把节点标记为成功，也不能把计划切换为 `EXECUTABLE`。
- `SUCCEEDED` 必须由执行器依据可信工具回执写入；缺少回执会转为失败。
- 结构校验通过只表示计划允许进入执行器，不能证明工具已经执行。
- 前端运行图不显示完整输入/输出摘要、账户、Token 或验证码，只显示状态、中文原因、回执摘要和非敏感引用编号。
