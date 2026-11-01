# Channel Gateway 设计与实现

## 1. 目标

Channel Gateway 将 APP、飞书、企业微信等渠道的消息转换为统一协议，再交给同一套 Agent Runtime。它只负责渠道身份映射、会话映射、消息幂等和回复编排，不获得银行资金执行权。

当前已实现通用网关、飞书长连接和 Webhook 双模式。长连接联调见 [`FEISHU_LONG_CONNECTION.md`](./FEISHU_LONG_CONNECTION.md)，Webhook 部署见 [`FEISHU_WEBHOOK.md`](./FEISHU_WEBHOOK.md)。两种模式只负责接收和回复，最终都进入同一 Channel Gateway。

## 2. 信任边界

系统将三个身份域严格分开：

1. Web/App 用户：通过 JWT 登录，可生成一次性渠道绑定码、查看和撤销自己的渠道身份。
2. 渠道适配器：通过 `X-Channel-Gateway-Key` 调用统一入站接口。真实 IM 接入后，适配器必须先完成平台 Webhook 验签。
3. 银行操作授权：黄色确认、红色强验证和生活图谱候选确认只能在受信 Web/App 中完成，IM 消息不能代替确认、短信验证码或人脸验证。

即使渠道 Webhook 已验签，也只能证明“消息来自该渠道”，不能证明用户已经授权某笔资金操作。

## 3. 数据模型

| 表 | 用途 | 关键约束 |
| --- | --- | --- |
| `channel_link_tokens` | 一次性绑定码 | 只保存 HMAC；单次使用；带过期时间 |
| `channel_identities` | 银行用户与外部渠道用户映射 | 渠道、租户、外部用户 HMAC 唯一 |
| `channel_conversations` | 外部会话与 Agent Session 映射 | 同一渠道身份和外部会话只对应一个 Agent Session |
| `channel_messages` | 入站消息、处理状态和最小回复快照 | 外部消息 HMAC 唯一；相同编号不同内容直接冲突 |
| `channel_worker_leases` | 长连接、Webhook 与调度 Worker 在线租约 | 心跳短时过期；进程标识只向用户展示脱敏指纹 |

外部用户 ID、外部会话 ID 和外部消息 ID 均使用 `CHANNEL_IDENTITY_PEPPER` 计算 HMAC。外部用户 ID 仅因主动私聊投递需要额外保存加密密文，该密文不进入用户 API；前端只显示脱敏 HMAC 指纹。回复快照只包含 Run ID、状态、提示文本和安全动作，不持久化账户余额等完整业务数据。

## 4. 绑定流程

```text
用户登录 Web/App
    -> POST /api/channels/link-tokens
    -> 获得 10 分钟有效的一次性绑定码
    -> 用户在已验签的 IM 会话中提交绑定码
    -> 适配器转换成 kind=LINK 的统一消息
    -> Channel Gateway 原子兑换绑定码并建立身份映射
```

绑定码数据库中只保存摘要。重复兑换、过期兑换，以及同一渠道身份抢绑到另一个银行用户都会被拒绝并写入渠道消息记录。
同一用户重新生成绑定码时，旧的未使用绑定码立即取消，避免多个有效口令并存。

## 5. 统一入站协议

适配器调用：

```http
POST /api/channels/messages/inbound
X-Channel-Gateway-Key: <仅适配器持有的共享密钥>
Content-Type: application/json
```

普通消息示例：

```json
{
  "schema_version": "1.0",
  "channel": "FEISHU",
  "tenant_id": "tenant_demo",
  "external_message_id": "om_xxx",
  "external_conversation_id": "oc_xxx",
  "external_user_id": "ou_xxx",
  "kind": "TEXT",
  "text": "给小王转 200 元",
  "occurred_at": "2026-10-30T10:00:00+08:00"
}
```

绑定消息把 `kind` 改为 `LINK`，删除 `text` 并提供 `link_code`。Schema 禁止同时发送 `text` 和 `link_code`，也不接受客户端传入 `user_id`、`session_id` 或权限级别。

## 6. 消息幂等

网关使用渠道、租户和外部消息 ID 的 HMAC 作为唯一范围：

- 首次消息：状态为 `PROCESSING`，完成后写为 `PROCESSED`、`REJECTED` 或 `FAILED`。
- 相同消息、相同内容：直接返回已保存的最小响应，并标记 `replayed=true`。
- 相同消息编号、不同内容：返回 `CHANNEL_MESSAGE_CONFLICT`，不会再次调用 Agent。
- 此前失败的消息：返回 `CHANNEL_MESSAGE_PREVIOUSLY_FAILED`，不会自动重复资金相关流程。
- Agent Run 同时使用 `channel:{gateway_message_id}` 作为第二层幂等键。

因此适配器超时重试不会产生重复 Run。若处理中断，系统保持保守状态，不会猜测业务已经成功。
飞书回复同时使用由 `gateway_message_id` 推导的稳定 UUID；平台重复投递时，即使再次调用回复 API，也不会产生第二条不同回复。

## 7. 回复与安全跳转

普通查询可以直接在渠道返回文本。遇到以下待办时，响应中的 `security.mode` 为 `APP_REQUIRED`：

| Agent 待办 | 网关动作 |
| --- | --- |
| `CONFIRMATION` | `OPEN_APP_CONFIRMATION` |
| `VERIFICATION` | `OPEN_APP_VERIFICATION` |
| `LIFE_GRAPH_CHANGE_REQUEST` | `OPEN_APP_CONFIRMATION` |

网关生成的 `deep_link` 只包含动作类型和不可猜测的 `run_id`，不在 IM 中返回验证码、确认令牌或可直接执行操作的 Bearer Token。飞书适配器将其渲染为安全操作卡片；用户进入 Web/App 后必须恢复登录，再由 `GET /api/agent/runs/{run_id}` 校验资源所有权和真实待办状态。URL 中声明的动作类型不能替代后端状态。

```text
飞书安全卡片
    -> /?secure_action=verification&run_id=run_xxx
    -> Web/App 恢复 JWT 会话
    -> 按当前用户读取 Run
    -> 对比 pending_action
    -> 展示确认或 MFA；越权、已处理和状态不一致均拒绝
```

## 8. API

| 方法 | 路径 | 鉴权 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/api/channels/link-tokens` | JWT | 为当前用户生成一次性绑定码 |
| `GET` | `/api/channels/identities` | JWT | 查看当前用户的渠道身份 |
| `DELETE` | `/api/channels/identities/{identity_id}` | JWT | 撤销渠道身份 |
| `POST` | `/api/channels/messages/inbound` | Gateway Key | 适配器提交统一入站消息 |
| `GET` | `/api/channels/status` | JWT | 查看配置、在线租约和脱敏 Worker 状态 |
| `POST` | `/api/channels/workers/heartbeat` | Gateway Key | Worker 续租并上报降级错误码 |
| `POST` | `/api/channels/webhooks/feishu` | 飞书签名 | 接收验签、解密后的飞书事件 |

## 9. 配置

在根目录 `.env` 中填写两个不同的随机密钥：

```dotenv
CHANNEL_GATEWAY_SECRET=<至少 32 位，仅渠道适配器持有>
CHANNEL_IDENTITY_PEPPER=<至少 32 位，仅后端持有>
CHANNEL_LINK_TOKEN_TTL_SECONDS=600
CHANNEL_WORKER_HEARTBEAT_SECONDS=20
CHANNEL_WORKER_LEASE_SECONDS=75
TRUSTED_APP_BASE_URL=http://127.0.0.1:5173
```

`TRUSTED_APP_BASE_URL` 只能是无查询参数、无片段的完整 HTTP(S) 地址。生产环境必须使用银行控制的 HTTPS 域名；本地 `127.0.0.1` 只适合同一台电脑上的飞书桌面端，手机无法访问电脑的回环地址。

可以分别生成随机值：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

不要与 `JWT_SECRET_KEY`、数据库密码或 DeepSeek Key 复用，也不要提交 `.env`。

新增数据表后执行：

```powershell
python -m alembic upgrade head
```

## 10. 已覆盖测试

- 绑定码只保存摘要、正常兑换和过期拒绝。
- 绑定码重放、跨用户抢绑和跨用户撤销拒绝。
- 未绑定消息不创建 Agent Run。
- 相同消息重放不重复执行，不同内容复用消息编号触发冲突。
- 大额转账在渠道中停留于 `WAITING_VERIFICATION`，只返回 App 强验证动作。
- 安全跳转只包含动作类型和 Run ID，不包含 Token、验证码或资金确认凭据。
- 飞书安全操作使用交互卡片，普通查询继续使用文本回复。
- 适配器密钥缺失或错误返回 401。
- 歧义协议负载返回统一的 `INVALID_REQUEST`。
- 用户无需接口工具即可生成绑定码、查看倒计时、脱敏身份、最近活跃时间并撤销。
- Worker 心跳有效时显示在线，租约过期或停止状态自动显示离线。
- Webhook 错误签名、错误令牌、过期时间窗和无效密文均在业务解析前拒绝。
- 长连接与 Webhook 重复投递同一消息时只产生一个 Agent Run，并使用相同回复 UUID。
