# 主动生活事件雷达说明

## 1. 功能边界

生活事件雷达负责扫描已确认生活图谱中的到期提醒，并通过已绑定用户的飞书私聊发送最小必要通知。当前覆盖生日、纪念日、学费日以及带截止日期的资金目标。

提醒只告知“有生活资金事件需要关注”并提供受信 Web/App 跳转，不包含关系人姓名、具体余额、完整目标金额或账户信息。飞书消息不能执行转账、扣款、目标修改、用户确认或 MFA；资金操作仍进入现有黄色确认或红色强验证链路。

## 2. 状态机

`scheduled_actions` 使用以下持久化状态：

| 状态 | 含义 | 可执行操作 |
| --- | --- | --- |
| `PENDING` | 等待到期 | 暂停、取消 |
| `PROCESSING` | Worker 持有有效租约 | 等待 Worker 完成 |
| `SENT` | 飞书返回成功回执 | 无 |
| `FAILED` | 本次失败，等待指数退避 | 暂停、取消 |
| `PAUSED` | 用户暂停 | 恢复、取消 |
| `CANCELED` | 用户取消或源事件失效 | 无 |
| `MANUAL_REVIEW` | 达到最大尝试次数 | 人工处理 |

Worker 使用 `SELECT ... FOR UPDATE SKIP LOCKED`、`lease_owner` 和 `lease_expires_at` 抢占动作。每次投递都使用由 `action_id` 派生的固定飞书 UUID；Worker 重启、租约过期或平台重试不会改变幂等键。

## 3. 数据与隐私

- 外部飞书用户 ID 的 HMAC 用于身份查重，Fernet 密文只供主动投递；API、日志和前端均不返回密文。
- 升级前的旧绑定没有投递密文。用户重新绑定，或从飞书再发送一条消息后，系统会安全回填。
- 投递前重新读取 `life_events`、`financial_goals` 和 `accounts.payload.available_balance`，不信任动作创建时的旧金额或旧余额。
- 事件或目标已经停用时，动作转为 `CANCELED` 并写审计，不发送过时提醒。
- 每次抢占、成功、失败、暂停、恢复、取消和转人工都会写入 `scheduled_action_audits`。

## 4. 配置与启动

先升级数据库：

```powershell
python -m alembic upgrade head
```

`.env` 需要已有的 MySQL、`CHANNEL_IDENTITY_PEPPER`、`FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`，并可调整：

```ini
LIFE_RADAR_POLL_SECONDS=30
LIFE_RADAR_BATCH_SIZE=20
LIFE_RADAR_LEASE_SECONDS=120
LIFE_RADAR_MAX_ATTEMPTS=3
LIFE_RADAR_RETRY_BASE_SECONDS=60
LIFE_RADAR_SIMULATION_ENABLED=false
```

启动独立雷达 Worker：

```powershell
python -m backend.app.integrations.life_event_radar_worker
```

只扫描一次用于排查：

```powershell
python -m backend.app.integrations.life_event_radar_worker --once
```

它与 Uvicorn、飞书入站长连接 Worker 是三个独立进程。雷达支持多实例，但比赛演示通常启动一个即可。

## 5. 用户接口

以下接口全部要求 Access Token，并按 JWT 当前用户隔离：

```text
GET  /api/life-radar/status
GET  /api/life-radar/actions
GET  /api/life-radar/actions/{action_id}/audits
POST /api/life-radar/actions/{action_id}/pause
POST /api/life-radar/actions/{action_id}/resume
POST /api/life-radar/actions/{action_id}/cancel
POST /api/life-radar/demo/scan
```

沙箱时间接口只有在本地、开发、测试或演示环境且 `LIFE_RADAR_SIMULATION_ENABLED=true` 时才启用。返回值必须包含：

```json
{
  "clock": {
    "mode": "SANDBOX_SIMULATED_TIME",
    "effective_now": "..."
  }
}
```

模拟扫描只处理当前 JWT 用户的动作，不能扫描其他用户。正式 Worker 始终使用 `REAL_TIME`。

## 6. 稳定错误码

| 错误码 | 处理建议 |
| --- | --- |
| `LIFE_RADAR_NOT_CONFIGURED` | 检查 MySQL、渠道密钥和飞书应用凭据 |
| `FEISHU_DELIVERY_ID_UNAVAILABLE` | 让当前用户重新绑定飞书或再发送一条消息 |
| `FEISHU_PROACTIVE_SEND_FAILED` | 等待指数退避；达到上限后人工处理 |
| `SCHEDULED_ACTION_IN_PROGRESS` | Worker 正在处理，稍后刷新 |
| `SCHEDULED_ACTION_NOT_CONTROLLABLE` | 动作已结束或进入人工处理 |
| `SCHEDULED_ACTION_LEASE_LOST` | 旧 Worker 停止写结果，由持有新租约的实例继续 |
| `LIFE_RADAR_SIMULATION_DISABLED` | 仅在本地演示环境显式开启模拟时间 |

任何错误都不会被表述为“提醒已发送”。只有飞书成功回执写入后，状态才会变为 `SENT`。
