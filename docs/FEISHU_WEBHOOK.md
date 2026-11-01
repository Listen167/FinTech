# 飞书 Webhook 安全部署

## 1. 适用范围

Webhook 模式适合已有公网 HTTPS 域名、反向代理和稳定部署环境的场景。本地开发优先使用长连接，不需要服务器，也不需要填写 Verification Token 或 Encrypt Key。

长连接和 Webhook 不应同时订阅同一个飞书应用的同一消息事件。无论选择哪种方式，消息都会转换为统一 `InboundChannelMessage`，再进入 Channel Gateway、Agent Runtime 和相同回复逻辑。

## 2. 环境变量

```dotenv
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=<应用密钥>
FEISHU_VERIFICATION_TOKEN=<事件订阅中的 Verification Token>
FEISHU_ENCRYPT_KEY=<事件订阅中的 Encrypt Key>
FEISHU_WEBHOOK_MAX_SKEW_SECONDS=300

CHANNEL_GATEWAY_SECRET=<至少 32 位内部密钥>
CHANNEL_IDENTITY_PEPPER=<至少 32 位身份 HMAC 密钥>
CHANNEL_WORKER_LEASE_SECONDS=75
TRUSTED_APP_BASE_URL=https://bank.example.com
```

这两个 Webhook 参数来自飞书开放平台的“事件与回调”安全配置，不是 App ID 或 App Secret。它们只能通过部署环境注入，不能写入仓库、日志或截图。

## 3. 回调地址

飞书控制台填写：

```text
https://api.example.com/api/channels/webhooks/feishu
```

公网入口必须使用受信 HTTPS。反向代理应保留以下请求头和原始请求体，不得在验签前重新序列化 JSON：

```text
X-Lark-Request-Timestamp
X-Lark-Request-Nonce
X-Lark-Signature
```

## 4. 安全处理顺序

```text
读取原始请求体
  -> 检查请求体大小
  -> 校验五分钟时间窗
  -> SHA-256 常量时间比较签名
  -> 使用 Encrypt Key 派生 AES-256-CBC 密钥并解密
  -> 校验 Verification Token
  -> 处理 URL challenge 或消息事件
  -> 转换为统一 Channel Gateway 协议
  -> 数据库消息唯一约束 + Agent 幂等键
  -> 飞书回复稳定 UUID
```

任一安全步骤失败都会返回稳定中文错误码，不会进入身份绑定或 Agent 执行。

## 5. 在线状态

Webhook 没有常驻飞书连接，因此“已配置”不等于“已观察到在线流量”。每次通过验签的消息事件都会续租 Webhook Worker 状态；超过租约时间没有可信事件后，管理页会显示未在线。该状态证明最近存在可信入站事件，不等同于外网全链路 SLA。

`GET /api/health` 和前端“渠道”工作区会分别展示：

- 飞书凭据是否配置；
- Webhook 安全参数是否配置；
- 最近是否有有效 Worker 租约；
- 每种接入方式的最近心跳时间。

## 6. 重放与失败边界

- 相同平台消息 ID 和相同内容只返回已保存响应，不会创建第二个 Run。
- 相同消息 ID 对应不同内容会触发 `CHANNEL_MESSAGE_CONFLICT`。
- Agent Run 使用 `channel:{gateway_message_id}` 再次保证幂等。
- 回复 UUID 从网关消息编号稳定推导，平台重试不会生成不同回复。
- 处理中进程异常时不会猜测业务成功；生产环境仍应增加队列、死信和告警。

## 7. 验收

1. 在前端“渠道”页确认 Webhook 显示“已配置”。
2. 飞书控制台保存回调地址，challenge 应成功完成。
3. 私聊机器人完成绑定并发送查询消息。
4. 刷新渠道页，Webhook 显示在线且身份最近活跃时间更新。
5. 重放相同事件，确认数据库只产生一个 Agent Run，回复 UUID 相同。
6. 使用错误签名或六分钟前的时间戳请求，确认在进入 Agent 前被拒绝。
