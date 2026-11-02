# Docker、域名与 HTTPS 部署说明

本文面向腾讯云轻量应用服务器的比赛部署环境。目标拓扑如下：

```text
浏览器 / 飞书安全跳转
        |
    80 / 443
        |
  Caddy 公网网关（自动 HTTPS）
        |----------------------|
        |                      |
  Nginx 前端静态站点       FastAPI /api
                               |
                 MySQL + 飞书 Worker + 雷达 Worker
```

只有 Caddy 暴露公网端口。MySQL `3306`、FastAPI `8000` 和前端容器端口均不对公网开放。

## 1. 域名与 IP 的关系

DNS 记录中的主机名必须是自己拥有并能管理解析的域名。仅在云服务器控制台填写一个域名，不代表已经注册或拥有该域名。

推荐使用独立子域名，例如：

```text
agent.example.com  A  <服务器公网 IPv4>
```

注意：

- 公网 DNS 只能填写服务器的公网 IP，不要填写 `10.x.x.x` 私网 IP。
- 同一主机名同时存在多个不同 IP 的 A 记录时，DNS 可能轮流返回这些 IP。
- 多 IP 只适用于已经做好负载均衡、数据同步和统一证书的多实例部署。
- 如果旧 IP 上还是旧项目，应删除旧 A 记录，或者为本项目使用新的 `agent` 子域名。
- `@` 表示根域名本身，`www`、`agent`、`api` 都是不同的主机名。
- 当前项目使用同源 `/api`，不需要额外创建 `api` 子域名。
- 不要创建 AAAA 记录，除非服务器已经启用并验证 IPv6 公网访问。

建议 TTL 临时设为 `600` 秒。Windows 本地检查命令：

```powershell
Resolve-DnsName agent.example.com
```

返回值中只应出现计划使用的公网 IP。DNS 缓存可能需要几分钟到数小时更新。

服务器位于中国大陆时，通过域名公开提供 Web 服务通常需要完成 ICP 备案。域名解析、HTTPS 证书签发和 ICP 备案是三件不同的事情；绑定服务器不能替代备案。备案尚未完成时可以先使用飞书长连接联调，因为长连接不要求公网 Webhook 地址。

## 2. 部署文件

仓库中的部署文件：

```text
backend/Dockerfile             FastAPI、迁移和 Worker 共用镜像
frontend/Dockerfile            Vue 构建与 Nginx 静态镜像
frontend/nginx.conf            SPA 静态文件和缓存规则
compose.yml                    六个运行服务与迁移任务
deploy/Caddyfile               HTTP/HTTPS 公网入口
deploy/compose.env.example     Compose 域名和镜像标签模板
deploy/app.env.example         应用环境变量模板
deploy/mysql.env.example       MySQL 独立凭据模板
```

运行服务包括：

| 服务 | 作用 | 公网端口 |
| --- | --- | --- |
| `gateway` | Caddy、自动 HTTPS、统一反向代理 | 80、443 |
| `frontend` | Nginx 前端静态文件 | 无 |
| `api` | FastAPI | 无 |
| `mysql` | MySQL 8.4 | 无 |
| `feishu-worker` | 飞书长连接消息接收 | 无 |
| `radar-worker` | 主动生活事件扫描和提醒 | 无 |
| `config-check` | 密钥、数据库与 HTTPS 配置前置校验 | 无 |
| `migrate` | Alembic 一次性迁移任务 | 无 |

## 3. 创建服务器目录

在服务器 SSH 终端执行：

```bash
sudo mkdir -p /opt/safebank-agent
sudo chown ubuntu:ubuntu /opt/safebank-agent
cd /opt/safebank-agent
```

优先通过受控 Git 仓库拉取代码。不要上传本地 `.env`、私钥、数据库备份或 API Key。

## 4. 创建生产配置

代码到达服务器后执行：

```bash
cd /opt/safebank-agent
cp deploy/compose.env.example .env
cp deploy/app.env.example .env.production
cp deploy/mysql.env.example .env.mysql
chmod 600 .env .env.production .env.mysql
```

分别编辑三个文件：

```bash
nano .env
nano .env.production
nano .env.mysql
```

`.env` 只保存 Compose 元数据：

```ini
APP_DOMAIN=agent.example.com
APP_IMAGE_TAG=latest
APP_ENV_FILE=.env.production
MYSQL_ENV_FILE=.env.mysql
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
NPM_REGISTRY=https://registry.npmmirror.com
```

`APP_DOMAIN` 只写主机名，不加 `https://`、端口或路径。暂时通过公网 IP 测试纯 HTTP 时可以使用：

```ini
APP_DOMAIN=:80
```

纯 HTTP 测试期间还必须在 `.env.production` 设置：

```ini
AUTH_COOKIE_SECURE=false
DEPLOYMENT_ALLOW_INSECURE_HTTP=true
TRUSTED_APP_BASE_URL=http://<服务器公网IP>
```

正式域名部署必须改为：

```ini
AUTH_COOKIE_SECURE=true
DEPLOYMENT_ALLOW_INSECURE_HTTP=false
TRUSTED_APP_BASE_URL=https://agent.example.com
```

生成随机密钥时，每个用途分别执行一次下面的命令，不要复用输出：

```bash
openssl rand -hex 32
```

至少需要生成并分别填写：

1. MySQL root 密码。
2. MySQL 应用用户密码。
3. `JWT_SECRET_KEY`。
4. `MFA_OTP_PEPPER`。
5. `FRAUD_RECIPIENT_PEPPER`。
6. `CHANNEL_GATEWAY_SECRET`。
7. `CHANNEL_IDENTITY_PEPPER`。

`.env.production` 的 `DB_PASSWORD` 必须与 `.env.mysql` 的 `MYSQL_PASSWORD` 完全一致。MySQL root 密码只存在 `.env.mysql`，不会注入 API 或 Worker 容器。

不要把三个环境文件的内容发到聊天、截图、Git 仓库或答辩材料中。

## 5. 校验和启动

先进行不会启动服务的配置校验：

```bash
docker compose config --quiet
```

拉取基础镜像并构建项目镜像：

```bash
docker compose pull mysql gateway
docker compose build api frontend
```

中国大陆服务器默认通过腾讯云 PyPI 镜像和 npmmirror 构建。它们只影响镜像构建，不影响 DeepSeek 或飞书运行时请求。若镜像源不可达，可在 `.env` 中替换 `PIP_INDEX_URL` 或 `NPM_REGISTRY` 后重试；Docker 会复用已完成的构建缓存。

验证生产变量和 Caddy 配置；两条命令都不会输出密钥值：

```bash
docker compose run --rm config-check
docker run --rm -e APP_DOMAIN=:80 -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.8-alpine caddy validate --config /etc/caddy/Caddyfile
```

首次启动核心服务：

```bash
docker compose up -d mysql migrate api frontend gateway
docker compose ps -a
```

`config-check` 和 `migrate` 显示 `Exited (0)` 是正常状态，分别表示生产配置校验和数据库迁移已完成。其他核心服务应为 `Up` 或 `healthy`。如果配置校验失败，执行 `docker compose logs config-check` 查看不包含密钥值的中文错误。

初始化两个演示账号密码：

```bash
docker compose exec api python -m scripts.init_demo_credentials
```

命令会交互式读取两组密码，只把 Argon2id 哈希写入 MySQL。

飞书配置确认无误后启动两个 Worker：

```bash
docker compose up -d feishu-worker radar-worker
docker compose ps -a
```

检查 API 容器内部健康状态：

```bash
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read().decode())"
```

有域名时检查公网 HTTPS：

```bash
curl -I https://agent.example.com
curl -fsS https://agent.example.com/api/health
```

Caddy 会在 DNS 已正确指向、80/443 入站端口可达后自动申请和续期证书。查看证书申请日志：

```bash
docker compose logs --tail=100 gateway
```

## 6. 常用排障命令

```bash
docker compose ps -a
docker compose logs --tail=100 mysql
docker compose logs --tail=100 migrate
docker compose logs --tail=100 api
docker compose logs --tail=100 feishu-worker
docker compose logs --tail=100 radar-worker
docker stats --no-stream
```

不要直接公开 `8000` 或 `3306` 来绕过网关排障。容器日志不得打印 `.env`、Access Token、Refresh Token、飞书密钥或数据库密码。

## 7. 数据持久化与备份

以下命令会停止容器，但保留数据库和 HTTPS 证书卷：

```bash
docker compose down
```

不要执行 `docker compose down -v`，因为 `-v` 会删除 MySQL 数据卷和 Caddy 证书卷。

备份数据库：

```bash
mkdir -p backups
chmod 700 backups
docker compose exec -T mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' > "backups/safebank-$(date +%Y%m%d-%H%M%S).sql"
chmod 600 backups/*.sql
```

备份文件包含业务和认证数据，不得提交到 Git 或用于公开演示。

## 8. 更新与回滚

更新代码后：

```bash
docker compose build api frontend
docker compose up -d
docker compose ps -a
```

数据库迁移由 `migrate` 服务在 API 启动前执行。发布前应先备份数据库。镜像标签可通过 `.env` 的 `APP_IMAGE_TAG` 固定为发布版本，避免无法确认当前运行代码。
