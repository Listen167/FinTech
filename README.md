# 安心办 SafeBank Guardian

面向家庭与银龄用户的安全银行 AI 智能体。项目将自然语言需求转换为可审计任务 DAG，通过动态绿、黄、红权限分级完成账单分析、订阅管理和智能转账，并针对提示词注入、诈骗语义、参数篡改、重放和连续验证失败提供独立安全控制。

> 核心原则：大模型负责理解与规划，后端负责授权与执行；没有银行工具回执，Agent 不得声称业务成功。

## 当前能力

| 场景 | 权限 | 已实现能力 |
| --- | --- | --- |
| 账单分析 | 绿色 | 分类统计、异常交易、周期扣费洞察、消费图表 |
| 订阅管理 | 黄色 | 闲置订阅识别、取消预览、明确确认、执行回执 |
| 智能转账 | 黄色/红色 | 中文参数提取、联系人核验、动态风险、确认/OTP、执行回执 |
| 跨场景联动 | 绿色分析 | 账单与订阅联合体检，写操作拆分确认 |
| 安全防护 | 独立控制 | 中英文注入拦截、诈骗语义、快照哈希、重放检测、失败熔断、审计 |
| 反诈冷静期 | 强制阻断 | 收款人 HMAC 指纹、跨 Run 改写重试阻断、事实证据、误报申诉、受信 App MFA 释放 |
| 用户隔离 | 后端强制 | 两个虚拟用户、资源所有权、跨用户访问拒绝、用户级幂等 |
| 会话恢复 | 后端强制 | 服务端生成会话 ID、会话列表、跨刷新与跨设备恢复 |
| 生活事件图 | 候选确认 | 多重家庭关系、生日/学费事件、资金目标冲突证据、LangGraph 恢复与人工接管 |
| 家庭资金协商 | 候选 + 二次确认 | DeepSeek 约束提取、确定性现金流、保证/条件方案、并发快照失效、撤销与有限 Consent Scope |
| 家庭守护圈 | 双方最小授权 | 一次性邀请、可撤销 Consent、风险摘要白名单、本人加守护人双确认、禁止代替 MFA 和资金执行 |
| 多渠道网关 | 后端强制 | 前端绑定与撤销、脱敏身份状态、统一消息协议、三层幂等、安全 App 跳转 |
| 飞书接入 | 渠道受限 | 长连接/Webhook 双模式、验签解密、Worker 在线租约、私聊多轮和安全操作卡片 |

所有资金和订阅操作均运行在 Mock Bank Sandbox 中，不连接真实银行、不处理真实资产。配置 MySQL 后，会话、授权、审计与沙箱状态持久化到数据库；数据库未配置或不可用时明确回退到内存仓库。

## 项目结构

```text
.
├── ai/
│   ├── agent/              # 意图解析、可选 LLM 适配、DAG 规划、统一入口
│   ├── safety/             # 注入检测与诈骗语义信号
│   ├── tools/              # Agent 工具白名单
│   └── evals/              # AI 安全与意图评测
├── backend/
│   ├── app/api/         # Agent Run、确认、验证、审计、演示重置 API
│   ├── app/mock_bank/      # 虚构账户、流水、联系人、订阅数据
│   ├── app/repositories/   # MySQL 持久仓库与内存回退
│   ├── app/services/       # 状态机、策略引擎、银行沙箱服务
│   └── tests/              # 后端与安全流程测试
├── frontend/src/
│   ├── api/                # API 客户端与类型
│   ├── components/         # 账单图表等组件
│   └── App.vue             # 比赛演示工作台
├── interfaces/openapi/     # 接口设计草案
└── docs/                   # 中文方案、架构、安全自评、实现与演示文档
```

## 本地运行

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

另开一个终端：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

访问 `http://localhost:5173`，接口文档位于 `http://localhost:8000/docs`。红色操作会生成随机短信验证码；本地演示可通过界面的“获取演示码”模拟短信到达。

Agent、会话、确认、验证、审计和演示重置接口均要求 `Authorization: Bearer <Access Token>`，用户身份只取自服务端校验后的 JWT。前端已接入登录、HttpOnly Refresh Cookie 自动续期、单设备退出和全部设备下线；Access Token 只保存在内存中，不写入浏览器持久化存储。

MySQL 配置写在根目录 `.env`，首次运行或模型变化后执行 `python -m alembic upgrade head`。访问 `http://localhost:8000/api/health`，看到 `persistence.backend` 为 `mysql`、`connected` 为 `true` 才表示持久化已启用。Navicat 刷新 `safebank_agent` 后可查看会话、Run、确认、验证、审计和模拟银行数据表。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests ai\evals -q
cd frontend
npm.cmd run build
```

## 可选大模型配置

系统默认使用确定性中文解析器，保证离线演示稳定。复制 `.env.example` 为 `.env` 并填写 `DEEPSEEK_API_KEY` 后，即可使用 OpenAI Python SDK 调用 DeepSeek 进行结构化意图解析。模型超时、API 错误或输出不符合 Pydantic Schema 时自动回退，且模型不可绕过后端策略。

详细步骤见 [DeepSeek 与多轮对话配置](docs/DEEPSEEK_SETUP.md)。

## 中文文档

- [统一使用说明书](docs/USER_MANUAL.md)
- [项目方案](docs/PROJECT_PROPOSAL.md)
- [系统架构](docs/ARCHITECTURE.md)
- [功能与实现指南](docs/IMPLEMENTATION_GUIDE.md)
- [任务图运行契约](docs/TASK_GRAPH_CONTRACT.md)
- [DeepSeek 与多轮对话配置](docs/DEEPSEEK_SETUP.md)
- [MySQL 持久化配置](docs/MYSQL_SETUP.md)
- [密码登录与短期 JWT 配置](docs/AUTH_SETUP.md)
- [Channel Gateway 设计与实现](docs/CHANNEL_GATEWAY.md)
- [飞书长连接接入与联调](docs/FEISHU_LONG_CONNECTION.md)
- [飞书 Webhook 安全部署](docs/FEISHU_WEBHOOK.md)
- [主动生活事件雷达](docs/LIFE_EVENT_RADAR.md)
- [家庭资金目标协商器](docs/FAMILY_FUNDING_NEGOTIATION.md)
- [反诈冷静期](docs/FRAUD_COOLING_PERIOD.md)
- [家庭守护授权圈](docs/GUARDIAN_CIRCLE.md)
- [开发顺序清单](docs/DEVELOPMENT_CHECKLIST.md)
- [获奖增强开发清单](docs/ENHANCEMENT_CHECKLIST.md)
- [安全自评报告](docs/SECURITY_SELF_ASSESSMENT.md)
- [答辩与演示脚本](docs/DEMO_SCRIPT.md)
- [获奖创新路线](docs/INNOVATION_ROADMAP.md)

