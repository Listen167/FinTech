# LangGraph 工作流实现说明

## 1. 使用范围

当前 LangGraph 只编排生活关系与事件流程，不负责保存关系图，也不直接执行资金操作。DeepSeek 继续负责结构化意图解析；权限、候选事务、冲突证据和审计继续由后端确定性服务负责。

核心实现文件：

- `backend/app/services/life_event_workflow.py`：状态、节点、路由与检查点。
- `backend/app/services/agent_runtime.py`：会话接入、检查点持久化、恢复和人工接管。
- `backend/app/services/life_graph_service.py`：候选确认与正式图谱原子写入。

## 2. 为什么不使用 LangGraph 内存检查点

比赛要求后端重启后仍能恢复，并且项目已经使用 MySQL。若使用 `InMemorySaver`，进程重启会丢失等待点；若另建 SQLiteSaver，又会产生第二套状态来源。

因此当前采用“LangGraph 负责计算下一阶段，MySQL Run 保存最小检查点”的方式：

```text
LangGraph 输出流程阶段
  -> AgentRuntime 提取最小 checkpoint
  -> MySQL agent_runs.payload.workflow
  -> 重启后重新读取候选请求等正式事实
  -> LangGraph RECOVER 节点决定恢复结果
```

这不是把业务数据塞进工作流。人物、事件、资金目标和候选快照仍在各自的 MySQL 表中。

## 3. 检查点示例

```json
{
  "engine": "langgraph",
  "version": 1,
  "stage": "WAITING_CONFIRMATION",
  "checkpoint_seq": 2,
  "trace": [
    "validate_slots",
    "create_candidate",
    "analyze_conflicts",
    "await_confirmation"
  ],
  "resumable": true,
  "recovered": false,
  "missing_slots": [],
  "change_request_id": "change_xxx",
  "conflict_status": "PRESSURE"
}
```

检查点不保存账户余额、验证码、密码、完整候选内容或第三方敏感信息。

## 4. 异常处理

| 情况 | 结果 |
| --- | --- |
| 生活图谱服务未配置 | `FAILED`，错误码 `LIFE_GRAPH_NOT_CONFIGURED` |
| 候选生成失败 | 进入 `candidate_failed`，不写入正式图谱 |
| 冲突检测失败 | 返回 `UNKNOWN`，不描述为安全 |
| 恢复时候选依据缺失 | `WAITING_HUMAN`，停止自动推进 |
| 同一候选重复确认 | `409 CHANGE_REQUEST_REPLAYED` |
| 转人工时冻结失败 | `409 HANDOFF_PREPARATION_FAILED`，原自动流程不变 |
| 跨用户转人工 | `403 RESOURCE_FORBIDDEN` |

## 5. 本地运行

安装新增依赖：

```powershell
pip install -r backend/requirements.txt
```

LangGraph 不需要新增环境变量，也不需要新增数据库迁移。重启后端即可加载状态图。

## 6. 验收路径

1. 输入“我爱人下个月生日，帮我留 1000 元买礼物”。
2. 检查 Run 为 `WAITING_INPUT`，轨迹为 `validate_slots -> await_input`。
3. 回复“18 号”，检查候选请求、冲突证据和 `WAITING_CONFIRMATION`。
4. 重启后端，刷新会话，检查 `workflow.recovered=true` 且候选未重复创建。
5. 确认候选，检查轨迹进入 `apply_decision`，正式图谱只写入一次。
6. 重新生成候选并点击“转人工”，检查候选被拒绝、Run 为 `WAITING_HUMAN`、审计包含 `HUMAN_HANDOFF`。
