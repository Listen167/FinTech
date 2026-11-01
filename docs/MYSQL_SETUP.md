# MySQL 持久化配置

## 1. 创建数据库账号

在 Navicat 使用管理员连接执行以下 SQL。密码应使用新的 ASCII 强密码，并与根目录 `.env` 中的 `DB_PASSWORD` 完全一致；不要保留示例占位文字，也不要将密码提交到 Git。

```sql
CREATE DATABASE IF NOT EXISTS safebank_agent
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'safebank_app'@'localhost'
  IDENTIFIED BY '替换为你的密码';

ALTER USER 'safebank_app'@'localhost'
  IDENTIFIED BY '替换为你的密码';

GRANT ALL PRIVILEGES ON safebank_agent.*
  TO 'safebank_app'@'localhost';

FLUSH PRIVILEGES;
```

## 2. 配置环境变量

```env
DATABASE_ENABLED=true
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=safebank_agent
DB_USER=safebank_app
DB_PASSWORD=与上一步相同的密码
```

## 3. 执行迁移

从项目根目录执行：

```powershell
python -m alembic upgrade head
```

再启动后端：

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000/api/health`。正确结果应包含：

```json
{
  "status": "ok",
  "persistence": {
    "backend": "mysql",
    "requested_backend": "mysql",
    "persistent": true,
    "connected": true
  }
}
```

如果返回 `backend: memory` 和 `fallback_reason`，说明应用已安全回退到内存模式。`OperationalError` 通常表示用户名、密码、端口或授权不匹配；修改后需重新启动后端。

## 4. Navicat 验证

刷新 `safebank_agent` 数据库后应看到 Agent 会话、任务、授权、审计以及模拟银行数据表。完成一轮对话后，可重点查看：

- `agent_sessions`
- `agent_runs`
- `confirmations`
- `verifications`
- `idempotency_keys`
- `audit_logs`
- `transfer_previews`
- `transactions`
- `subscriptions`

比赛版为了兼容现有状态机，以独立检索列加 JSON 快照保存状态。生产环境应进一步使用行级事务、乐观锁和数据库故障告警。
