# 后端服务

FastAPI 服务负责 Agent Run 状态、动态权限、确认、强验证、渠道身份、消息幂等、审计和 Mock Bank Sandbox。AI 只能提出计划，最终写操作均在此层校验。

从仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

Agent 沙箱在数据库不可用时可以回退到内存；用户认证、生活图谱和 Channel Gateway 必须使用 MySQL。可调用 `POST /api/demo/reset` 恢复演示业务数据，渠道配置见 `docs/CHANNEL_GATEWAY.md`。

飞书长连接必须作为独立进程启动：

```powershell
python -m backend.app.integrations.feishu_long_connection
```

它通过受保护的内部 HTTP 接口调用 FastAPI，不直接操作 Agent 仓库。配置与绑定流程见 `docs/FEISHU_LONG_CONNECTION.md`。

