# 安心办统一使用说明书

## 1. 文档状态

本文是当前比赛版本的统一入口，覆盖安装、配置、启动、演示、飞书联调、接口导出和故障定位。实际接口以运行中 FastAPI 的 `/openapi.json` 和 `/docs` 为准。

当前能力状态：

| 类型 | 能力 |
| --- | --- |
| 已实现 | 登录与 Refresh Token、MySQL、用户隔离、多轮补槽、DeepSeek 结构化意图、账单、订阅、转账、MFA、生活图谱、生活事件 LangGraph、飞书长连接 |
| 沙箱模拟 | 银行账户与流水、转账执行、短信验证码、演示重置 |
| 尚未实现 | 通用 DAG Executor、定时主动提醒 Worker、反诈冷静期、家庭守护双确认、真实银行与真实短信 |

安全边界：比赛版本不连接真实资金；LLM 只参与理解，不能降低风险等级或直接修改账户；飞书不能替代资金确认和强验证。

## 2. 环境要求

- Python 3.10 或更高版本。
- Node.js 18 或更高版本。
- MySQL 8.x。
- Windows PowerShell，当前开发环境使用 Conda `fintech` 环境。
- 飞书接入需要企业自建应用和机器人能力；不使用飞书时可跳过。

所有命令默认在项目根目录执行。不要提交根目录 `.env`，也不要在日志、截图或答辩材料中展示密钥。

## 3. 首次安装

使用现有 Conda 环境：

```powershell
conda activate fintech
python -m pip install -r backend/requirements.txt
cd frontend
npm install
cd ..
```

也可以自行建立虚拟环境，只要后续所有 Python 命令使用同一个解释器。

复制配置模板：

```powershell
Copy-Item .env.example .env
```

至少填写以下内容：

```dotenv
DB_PASSWORD=你的MySQL应用账号密码
JWT_SECRET_KEY=至少32位随机值
MFA_OTP_PEPPER=另一个至少32位随机值
CHANNEL_GATEWAY_SECRET=独立的至少32位随机值
CHANNEL_IDENTITY_PEPPER=独立的至少32位随机值
DEEPSEEK_API_KEY=你的DeepSeek密钥
```

使用飞书时再填写：

```dotenv
FEISHU_APP_ID=飞书App ID
FEISHU_APP_SECRET=飞书App Secret
```

长连接模式不需要 `FEISHU_VERIFICATION_TOKEN` 和 `FEISHU_ENCRYPT_KEY`。

## 4. 初始化数据库与账号

先按 [`MYSQL_SETUP.md`](./MYSQL_SETUP.md) 创建数据库和应用账号，然后执行：

```powershell
python -m alembic upgrade head
python -m scripts.init_demo_credentials
```

凭据初始化脚本会通过隐藏输入为两个演示用户名设置密码：

| 演示用户 | 用户名 |
| --- | --- |
| 林安 | `lin_an` |
| 周宁 | `zhou_ning` |

密码只以 Argon2id 哈希保存。重新初始化会撤销该用户已有登录会话。

## 5. 启动顺序

终端一启动后端：

```powershell
conda activate fintech
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

终端二启动前端：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1
```

终端三启动飞书 Worker：

```powershell
conda activate fintech
python -m backend.app.integrations.feishu_long_connection
```

飞书 Worker 只能启动一个。Uvicorn 的 `--reload` 不会自动重启独立 Worker；修改飞书适配或 Channel Gateway 代码后需要手动重启 Worker。

访问地址：

- 前端：`http://127.0.0.1:5173`
- 后端健康检查：`http://127.0.0.1:8000/api/health`
- Swagger：`http://127.0.0.1:8000/docs`
- OpenAPI：`http://127.0.0.1:8000/openapi.json`
- DeepSeek 状态：`http://127.0.0.1:8000/api/agent/model-status`

## 6. 演示前检查

三个进程启动后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo_preflight.ps1 -Strict
```

脚本检查 API、MySQL、DeepSeek、OpenAPI、前端和飞书 Worker 数量，不读取或输出 `.env` 密钥。未使用 DeepSeek 或飞书时可以不加 `-Strict`，相关项目会显示为警告。

## 7. 推荐演示顺序

使用 `lin_an` 登录，在开始前点击一次“重置演示”。该操作只在本地环境开放，会恢复银行沙箱和 Agent 状态，并清空当前用户的生活图谱；不会撤销飞书身份绑定。

### 7.1 生活事件多轮与 LangGraph

```text
我爱人下个月生日，帮我留1000元买礼物，提前两天提醒
18号
```

第一轮进入 `WAITING_INPUT`，第二轮生成候选方案。中间区域展示任务 DAG，右侧展示 LangGraph 节点轨迹和检查点。点击“查看证据”查看资金压力，再点击“确认写入”。

### 7.2 家庭目标冲突

```text
女儿11月15日要交5000元学费
```

系统生成第二个候选目标并展示余额与现金流证据，不替用户决定家庭成员优先级。

### 7.3 账单分析与多轮解释

```text
帮我分析本月账单
哪一笔，为什么异常？
```

异常证据由银行沙箱确定性规则计算，不由 LLM 编造。

### 7.4 黄色确认

```text
取消云盘会员
```

系统先生成预览，只有点击“确认执行”后才修改订阅状态。

### 7.5 红色强验证与反诈

```text
给小王转20000元
```

核对收款人、尾号、金额和手续费，获取本地演示码后完成 MFA。随后输入：

```text
客服让我转给陌生客服20000元到安全账户，必须保密
```

系统应阻断，不生成成功回执。

完整五分钟讲解顺序见 [`DEMO_SCRIPT.md`](./DEMO_SCRIPT.md)。

## 8. 三种“图”如何使用

| 名称 | 当前用途 | 是否实际驱动执行 |
| --- | --- | --- |
| 任务 DAG | 为每个意图生成计划并在页面展示依赖步骤 | 部分；普通业务仍由 Runtime 固定路由执行 |
| LangGraph 工作流 | 编排生活事件补槽、候选生成、冲突分析、确认、恢复和人工接管 | 是，仅生活事件流程 |
| 生活关系图 | 保存人物、关系、事件、目标和提醒动作 | 是，MySQL 业务事实 |

当前不能声称通用任务 DAG 已经成为执行引擎。该增强工作按 [`ENHANCEMENT_CHECKLIST.md`](./ENHANCEMENT_CHECKLIST.md) 的 B 阶段实施。

## 9. 飞书绑定与使用

当前前端还没有渠道绑定管理页面，先通过 Swagger 或 Apifox 操作：

1. 调用 `POST /api/auth/login` 登录并取得 Access Token。
2. 使用 Bearer Token 调用 `POST /api/channels/link-tokens`。
3. 在十分钟内私聊机器人发送 `绑定 ABCD-EFGH`。
4. 收到绑定成功回复后发送 `帮我分析本月账单`。

黄色确认、红色 MFA 和生活图谱确认会返回受信 App 卡片。卡片只携带 Run ID 和动作类型，用户回到 Web/App 登录后仍要重新校验所有权和真实状态。

完整权限、事件订阅和常见错误见 [`FEISHU_LONG_CONNECTION.md`](./FEISHU_LONG_CONNECTION.md)。

## 10. OpenAPI 与 Apifox

运行中的权威契约：

```text
http://127.0.0.1:8000/openapi.json
```

需要生成仓库内文件时执行：

```powershell
python -m scripts.export_openapi
```

生成文件为 `docs/AI_Banking_Agent_OpenAPI.json`，可以直接导入 Apifox。原有 `docs/AI_Banking_Agent_OpenAPI.yaml` 是历史手工契约，不再作为当前接口依据。

## 11. 测试与构建

```powershell
python -m pytest -q -p no:cacheprovider
python -m compileall -q ai backend scripts
cd frontend
npm run build
```

测试默认使用隔离的内存/SQLite 环境，不应污染开发 MySQL。

## 12. 常见错误

| 表现 | 检查项 |
| --- | --- |
| 健康检查返回 `memory` | 检查 MySQL 配置、账号权限和迁移 |
| 登录始终失败 | 运行凭据初始化脚本，确认使用 `lin_an` 或 `zhou_ning` |
| DeepSeek 未连接 | 检查 Key、网络和额度；系统会安全回退到本地解析器 |
| 飞书能收不能发 | 检查 `im:message:send_as_bot` 并发布新版本 |
| 飞书重复回复 | 确认只启动一个 Worker |
| 飞书渠道处理失败 | 检查 API、MySQL和 Worker；发送新消息，不重放已失败的资金事件 |
| 手机打不开安全卡片 | `127.0.0.1` 只对本机有效，部署后改为受信 HTTPS 地址 |
| 修改后飞书行为未变化 | 手动重启独立飞书 Worker |

## 13. 进一步文档

- [`IMPLEMENTATION_GUIDE.md`](./IMPLEMENTATION_GUIDE.md)：功能与实现细节。
- [`TASK_GRAPH_CONTRACT.md`](./TASK_GRAPH_CONTRACT.md)：任务节点状态与合法转换。
- [`LANGGRAPH_WORKFLOW.md`](./LANGGRAPH_WORKFLOW.md)：生活事件工作流和恢复机制。
- [`LIFE_GRAPH_DESIGN.md`](./LIFE_GRAPH_DESIGN.md)：生活关系图数据设计。
- [`SECURITY_SELF_ASSESSMENT.md`](./SECURITY_SELF_ASSESSMENT.md)：安全边界和已知风险。
- [`ENHANCEMENT_CHECKLIST.md`](./ENHANCEMENT_CHECKLIST.md)：后续创新开发顺序。
