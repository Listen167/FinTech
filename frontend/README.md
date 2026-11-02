# 前端演示工作台

基于 Vue 3、TypeScript 和 ECharts，集中展示自然语言输入、任务 DAG、账单图表、订阅、转账确认、强验证、动态风险和操作护照。

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

开发模式默认连接与页面相同主机名的 `8000` 端口，可通过 `VITE_API_BASE_URL` 修改。生产构建默认使用同源 `/api`，由 Caddy 将 API 请求转发到后端容器，不应在浏览器中直接开放 `8000` 端口。前后端应使用相同主机名访问，例如都使用 `localhost` 或都使用 `127.0.0.1`，避免 Cookie 域不一致。

## 登录与会话

- 使用 `lin_an` 或 `zhou_ning` 以及初始化时设置的密码登录。
- Access Token 只保存在前端模块内存，不写入 `localStorage` 或 `sessionStorage`。
- Refresh Token 由后端写入 HttpOnly Cookie，前端不能读取。
- 页面刷新或业务请求返回 401 时，前端通过 CSRF 双提交调用刷新接口，并且只自动重试原请求一次。
- 退出登录会清除当前用户的本地 Agent 会话；“全部下线”同时撤销该用户所有服务端登录会话。
- 用户切换时工作台状态会清空；Agent 会话 ID 由后端生成并保存在 MySQL，前端登录后恢复该用户最近会话。

如修改后端 `AUTH_CSRF_COOKIE_NAME`，前端构建环境的 `VITE_AUTH_CSRF_COOKIE_NAME` 必须保持一致。生产环境必须使用 HTTPS，并配置后端 Cookie 的 `Secure=true`。仅在公网 IP 的临时 HTTP 联调阶段使用 `DEPLOYMENT_ALLOW_INSECURE_HTTP=true` 与 `AUTH_COOKIE_SECURE=false`；切换域名 HTTPS 后必须恢复安全配置。

