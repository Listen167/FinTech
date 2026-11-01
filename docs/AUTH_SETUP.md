# 密码登录与短期 JWT 配置

## 1. 生成 JWT 密钥

在项目根目录执行：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将输出只写入根目录 `.env`，不要粘贴到聊天、截图或 Git：

```env
JWT_SECRET_KEY=这里填写随机输出
JWT_ALGORITHM=HS256
JWT_ISSUER=safebank-agent
JWT_AUDIENCE=safebank-web
JWT_ACCESS_MINUTES=10
AUTH_MAX_FAILED_ATTEMPTS=5
AUTH_LOCK_MINUTES=10
AUTH_SESSION_DAYS=7
MFA_OTP_PEPPER=另一个独立随机值
MFA_CODE_TTL_SECONDS=300
MFA_MAX_ATTEMPTS=3
MFA_RESEND_COOLDOWN_SECONDS=30
```

两个密钥都至少 32 个字符，并且不应相同。当前 JWT 只允许 `HS256`，防止通过配置降级到未批准算法；未配置 `MFA_OTP_PEPPER` 时本地环境会回退使用 JWT 密钥，但比赛交付和生产部署应使用独立密钥。

## 2. 初始化两个演示账号

```powershell
python -m scripts.init_demo_credentials
```

脚本会通过隐藏输入分别设置：

| 用户 | 用户名 |
| --- | --- |
| 林安 | `lin_an` |
| 周宁 | `zhou_ning` |

比赛演示密码必须为 8 至 128 个字符，并同时包含至少一个字母和一个数字；大小写组合和符号不作强制要求。脚本只向 `password_credentials` 写入 Argon2id 哈希；重新运行会提升凭据版本并撤销该用户已有登录会话。生产环境应接入银行统一身份认证并采用更严格的密码策略。

## 3. 启动与检查

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000/api/auth/status`，应看到：

```json
{
  "configured": true,
  "algorithm": "HS256",
  "issuer": "safebank-agent",
  "audience": "safebank-web",
  "access_minutes": 10,
  "refresh_enabled": true
}
```

可在 `http://127.0.0.1:8000/docs` 调用：

- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `GET /api/auth/me`
- `POST /api/auth/logout`
- `POST /api/auth/logout-all`

## 4. 安全行为

- Access Token 有效期为 10 分钟，要求 `sub`、`sid`、`jti`、`iss`、`aud`、`iat`、`nbf`、`exp`、`token_type` 和 `credential_version`。
- 连续 5 次密码错误会锁定账号 10 分钟。
- 登录失败、锁定、Token 拒绝和注销写入 `auth_events`。
- 审计只保存用户名和 IP 的 HMAC 指纹，不保存原始密码、Token 或完整 IP。
- 注销、密码重新初始化、用户禁用或凭据版本变化会使服务端会话失效。
- 登录成功后，原始 Refresh Token 只写入 `safebank_refresh` HttpOnly Cookie，数据库仅保存哈希。
- `safebank_csrf` Cookie 可由前端读取；调用刷新接口时必须把它放入 `X-CSRF-Token` 请求头。
- 每次刷新同时轮换 Refresh Token 和 CSRF Token。旧 Refresh Token 再次出现时，系统记录 `REFRESH_REUSE_DETECTED` 并撤销整条登录会话。
- 本地开发的 Cookie `Secure=false`；生产 HTTPS 部署必须配置 `AUTH_COOKIE_SECURE=true`。
- Step-up MFA 的六位验证码不写入数据库，数据库只保存带密钥 HMAC 摘要。
- 红色操作的验证码绑定用户、Run 和操作快照，五分钟过期、成功后单次使用、连续错误三次熔断。
- `/api/demo/verifications/{id}/code` 仅供本地比赛演示；非本地环境不可用。
