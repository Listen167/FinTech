# 安心办统一使用说明书

## 1. 文档状态

本文是当前比赛版本的统一入口，覆盖安装、配置、启动、演示、飞书联调、接口导出和故障定位。实际接口以运行中 FastAPI 的 `/openapi.json` 和 `/docs` 为准。

当前能力状态：

| 类型 | 能力 |
| --- | --- |
| 已实现 | 登录与 Refresh Token、MySQL、用户隔离、多轮补槽、DeepSeek 结构化意图、可执行任务图、操作护照、账单、订阅、转账、MFA、生活图谱、生活事件 LangGraph、飞书长连接、主动生活事件雷达、家庭资金协商、反诈冷静期、家庭守护授权圈 |
| 沙箱模拟 | 银行账户与流水、转账执行、短信验证码、演示重置 |
| 尚未实现 | 真实银行、真实短信、银行统一身份与真实可信联系人核验 |

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

终端四启动生活事件雷达 Worker：

```powershell
conda activate fintech
python -m backend.app.integrations.life_event_radar_worker
```

雷达 Worker 使用数据库租约，可部署多个实例；本地演示启动一个即可。首次启用前必须运行 `python -m alembic upgrade head`。详细配置和沙箱时间演示见 [`LIFE_EVENT_RADAR.md`](./LIFE_EVENT_RADAR.md)。

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

### 7.3 家庭资金目标协商

先在生活图谱确认至少两个带截止日期的资金目标，再点击顶部“资金协商”：

1. 对每个目标明确选择刚性或弹性。
2. 弹性目标填写最低金额，并选择是否允许缩减、延期或分期。
3. 填写不可动用备用金后点击“计算方案”。
4. 比较保证方案、条件方案和不可行方案的逐日余额与缺口。
5. 填写选择理由，选择方案后再执行二次确认。

选择阶段不会修改目标；二次确认前余额或目标发生变化时，旧方案会失效并自动重新计算。确认只修改正式目标金额、日期或资金策略，不预留余额、不转账、不扣款。已应用的目标变更可以撤销，但目标如果被后续操作再次修改，系统会停止自动撤销并要求人工处理。

完整算法、状态流和接口见 [`FAMILY_FUNDING_NEGOTIATION.md`](./FAMILY_FUNDING_NEGOTIATION.md)。

### 7.4 家庭守护授权圈

点击顶部“守护圈”，使用两个演示账号完成双方授权：

1. `lin_an` 指定 `user_002`，选择大额、新收款人或疑似诈骗场景，设置金额上限、可见字段和截止日期。
2. 生成一次性邀请令牌。令牌只展示一次，数据库只保存摘要。
3. 切换到 `zhou_ning`，粘贴令牌并接受明确范围。生活图谱中的家庭身份不会自动授予权限。
4. 回到 `lin_an`，创建风险摘要并先执行本人确认。
5. `zhou_ning` 只能看到授权字段和金额区间，可确认“风险已知”或建议停止。
6. 任一方撤销后，旧摘要和待确认立即失效。

守护确认不满足 MFA、不授权转账，也不自动执行任何资金操作。完整状态机和接口见 [`GUARDIAN_CIRCLE.md`](./GUARDIAN_CIRCLE.md)。

### 7.5 账单分析与多轮解释

```text
帮我分析本月账单
哪一笔，为什么异常？
```

异常证据由银行沙箱确定性规则计算，不由 LLM 编造。

### 7.6 黄色确认

```text
取消云盘会员
```

系统先生成预览，只有点击“确认执行”后才修改订阅状态。

### 7.7 红色强验证与反诈

```text
给小王转20000元
```

核对收款人、尾号、金额和手续费，获取本地演示码后完成 MFA。随后输入：

```text
客服让我转给陌生客服20000元到安全账户，必须保密
```

系统进入 `COOLING_OFF`，展示命中的事实证据、北京时间结束时间、96110 求助入口和收款人指纹提示。随后换一种说法再次向“陌生客服”转账，系统仍会关联同一风险事件并阻断；向其他正常收款人转账不受影响。

误报申诉只记录理由，不会自动解除。只有受信 Web/App 可以创建释放验证码；飞书不能解除。验证成功后原转账仍不会自动执行，如确需转账必须重新发起并重新接受风控。

完整五分钟讲解顺序见 [`DEMO_SCRIPT.md`](./DEMO_SCRIPT.md)。

## 8. 三种“图”如何使用

| 名称 | 当前用途 | 是否实际驱动执行 |
| --- | --- | --- |
| 可执行任务图 | 用白名单处理器执行节点，并保存状态、依赖、回执与恢复检查点 | 是，覆盖核心比赛流程 |
| LangGraph 工作流 | 编排生活事件，以及家庭资金约束、时间线、方案选择、候选和二次确认 | 是，业务写入仍由确定性服务事务执行 |
| 生活关系图 | 保存人物、关系、事件、目标和提醒动作 | 是，MySQL 业务事实 |

任务图只能执行后端显式注册的白名单处理器，不能运行模型生成代码。

## 9. 飞书绑定与使用

登录前端并点击顶部“渠道”：

1. 点击“生成飞书绑定码”，确认页面倒计时仍有效。
2. 点击“复制绑定口令”，在飞书中私聊机器人发送 `绑定 ABCD-EFGH`。
3. 收到绑定成功回复后刷新渠道页，核对脱敏指纹、最近活跃时间和接入在线状态。
4. 发送 `帮我分析本月账单`，再追问“哪一笔，为什么异常？”。
5. 演示结束可在同一页面点击“撤销绑定”；旧飞书会话随后必须重新绑定。

黄色确认、红色 MFA 和生活图谱确认会返回受信 App 卡片。卡片只携带 Run ID 和动作类型，用户回到 Web/App 登录后仍要重新校验所有权和真实状态。

长连接联调见 [`FEISHU_LONG_CONNECTION.md`](./FEISHU_LONG_CONNECTION.md)，公网 Webhook 部署见 [`FEISHU_WEBHOOK.md`](./FEISHU_WEBHOOK.md)。两种模式共用同一 Channel Gateway 和幂等逻辑，不要同时为同一应用启用两套事件订阅。

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

### 11.1 评测与红队基线

```powershell
python scripts/generate_eval_dataset.py
python scripts/run_evaluation.py
python -m pytest ai/evals/test_evaluation_framework.py backend/tests/test_observability.py backend/tests/test_red_team_regression.py -q
```

登录前端后可进入“评测”工作区查看聚合指标。默认报告是离线确定性基线，不调用 DeepSeek，模型回退率因此为 100%。只有显式运行 `python scripts/run_evaluation.py --live-llm` 才会调用 DeepSeek 并产生在线报告。详细指标、隐私边界和已知误差见 `docs/EVALUATION_AND_OBSERVABILITY.md`。

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
| 主动提醒未发送 | 检查雷达 Worker、飞书绑定密文和 `/api/life-radar/status`；旧绑定需重新绑定或再发送一条飞书消息 |
| 提醒进入人工处理 | 查看动作审计和最后错误码，修复渠道后由人工决定是否创建新提醒 |

## 13. 进一步文档

- [`IMPLEMENTATION_GUIDE.md`](./IMPLEMENTATION_GUIDE.md)：功能与实现细节。
- [`TASK_GRAPH_CONTRACT.md`](./TASK_GRAPH_CONTRACT.md)：任务节点状态与合法转换。
- [`LANGGRAPH_WORKFLOW.md`](./LANGGRAPH_WORKFLOW.md)：生活事件工作流和恢复机制。
- [`LIFE_GRAPH_DESIGN.md`](./LIFE_GRAPH_DESIGN.md)：生活关系图数据设计。
- [`FAMILY_FUNDING_NEGOTIATION.md`](./FAMILY_FUNDING_NEGOTIATION.md)：家庭资金协商算法、状态流与安全边界。
- [`FRAUD_COOLING_PERIOD.md`](./FRAUD_COOLING_PERIOD.md)：跨 Run 反诈风险事件、申诉与强验证释放。
- [`GUARDIAN_CIRCLE.md`](./GUARDIAN_CIRCLE.md)：家庭守护最小授权、一次性邀请与风险双确认。
- [`EVALUATION_AND_OBSERVABILITY.md`](./EVALUATION_AND_OBSERVABILITY.md)：评测数据、红队门禁、聚合指标和隐私边界。
- [`SECURITY_SELF_ASSESSMENT.md`](./SECURITY_SELF_ASSESSMENT.md)：安全边界和已知风险。
- [`ENHANCEMENT_CHECKLIST.md`](./ENHANCEMENT_CHECKLIST.md)：后续创新开发顺序。
