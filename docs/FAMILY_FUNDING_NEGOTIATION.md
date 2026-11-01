# 家庭资金目标协商器

## 1. 解决的问题

生活图谱能够发现“学费、生日礼物等目标在同一时期争用资金”，但冲突提示本身不能回答如何调整。家庭资金目标协商器把冲突升级为一条可验证流程：

1. DeepSeek 把生活语言提取为严格约束，不能直接修改目标。
2. 确定性算法按截止日期重放现金流并证明方案是否可行。
3. LangGraph 保存补充约束、计算、选择、候选和确认阶段。
4. 用户决定刚性、弹性和价值优先级，家庭身份不产生资金支配权。
5. 二次确认前重新读取银行余额与正式目标；数据变化会使旧方案失效。

比赛版只修改已确认的生活图谱目标金额、日期和资金策略，不预留余额、不转账、不扣款。

## 2. 责任边界

| 参与者 | 可以做什么 | 不可以做什么 |
| --- | --- | --- |
| DeepSeek | 提取约束、解释方案、提出额外候选 | 声称收入已到账、绕过最低金额、决定家庭成员优先级、直接执行写操作 |
| 确定性规划器 | 计算逐日余额、缺口、保证/条件/不可行状态，校验模型候选 | 替用户决定价值选择 |
| LangGraph | 编排阶段、保存轨迹和恢复检查点 | 绕过服务层事务和确认 |
| 用户 | 确认刚柔性、最低金额、备用金、方案与选择理由 | 通过前端伪造银行余额 |
| 银行后端 | 提供当前余额、目标事实、事务锁、审计和执行边界 | 将预测收入伪装成确认收入 |

人物关系只写入 `relationship_context`，规划器的排序键只包含刚柔性、截止日期、用户显式优先级和稳定目标编号。

## 3. 数据契约

核心 Schema 位于 `backend/app/schemas/family_funding.py`。

### 3.1 目标约束

- `target_amount`：当前正式目标金额，确认时由数据库重新读取。
- `minimum_amount`：用户接受的最低金额；刚性目标必须等于目标金额。
- `due_date`：截止日期。
- `rigidity`：`RIGID` 或 `FLEXIBLE`。
- `can_reduce`、`can_delay`、`can_installments`：用户明确允许的调整方式。
- `priority_rank`：可选的用户显式优先级；不能由人物关系推断。
- `reserved_amount`：已经预留的事实金额，目标不能缩减到它以下。

### 3.2 全局约束

- 不可动用备用金。
- 已承诺支出。
- 允许使用的账户编号。
- 规划开始和结束日期。
- 已确认收入和预测收入。

### 3.3 证据来源

每个约束可保存来源与 0-10000 置信基点：

- `USER_CONFIRMED`：用户明确确认。
- `BANK_CONFIRMED`：当前银行沙箱事实。
- `MODEL_INFERRED`：模型推断，只能辅助解释。
- `FORECAST`：预测收入，只能进入条件余额。

只有 `USER_CONFIRMED` 和 `BANK_CONFIRMED` 收入能进入保证余额。`MODEL_INFERRED` 和 `FORECAST` 即使置信度很高，也不能把方案标为保证方案。

## 4. 确定性计算

规划器位于 `backend/app/services/family_funding_planner.py`。每个事件日计算：

```text
期末保证余额 = 期初余额 + 已确认收入 - 承诺支出 - 到期目标分配
保证缺口 = max(备用金 - 期末保证余额, 0)
条件余额 = 保证余额 + 当日模型推断或预测收入
```

同一截止日内先检验刚性目标，再检验弹性目标；同类目标按用户显式优先级、截止日期和稳定目标编号排序。规划器不会读取“妻子、女儿、父母”等关系字段作为排序条件。

输出状态：

- `GUARANTEED`：不依赖预测收入，所有日期均守住备用金。
- `CONDITIONAL`：需要后续确认收入或重新校验，不能描述为资金保证。
- `INFEASIBLE`：即使计入当前条件证据仍有缺口，不能进入确认阶段。

内置方案包含保持现状、缩减弹性金额、延期等待确认收入和分期。LLM 可以提出 `CUSTOM` 方案，但会重新经过同一个规划器；低于最低金额、低于已预留金额、增加目标或非法延期会被丢弃，并记录 `MODEL_PLAN_REJECTED` 审计。

## 5. LangGraph 状态流

`backend/app/services/family_funding_workflow.py` 实际使用 `StateGraph`，节点如下：

```text
load_context
  -> complete_constraints
  -> await_input / build_timeline
  -> calculate_plans
  -> await_selection
  -> create_candidate
  -> await_confirmation
  -> apply_confirmation
```

拒绝、取消、人工接管和撤销分别进入独立终止节点。数据库保存 `stage`、`checkpoint_seq`、`trace`、是否可恢复和稳定错误码；真实写入仍由服务层事务完成。

## 6. 候选、确认与并发安全

1. 创建协商时，后端用当前账户和目标生成基线快照哈希。
2. 选择方案时再次读取账户和目标；变化则重新计算，不生成旧候选。
3. 选择成功只保存 `selected_plan_id` 和候选快照，不修改正式目标。
4. 二次确认在同一事务中锁定协商记录、账户和目标。
5. 快照不同则把状态退回 `WAITING_SELECTION`，保存新方案并返回 `FUNDING_SNAPSHOT_CHANGED`。
6. 快照一致才修改目标金额、日期或 `funding_policy`，并保存 before/after。
7. 撤销前再次检查目标仍等于 after；后续变化会返回 `FUNDING_UNDO_CONFLICT` 并要求人工处理。

整个流程不修改 `accounts.payload.available_balance`，也不会创建转账或扣款记录。

## 7. Consent Scope

`funding_consent_scopes` 保存：目标范围、金额上下限、备用金、单次调整上限、有效期和撤销时间。比赛版 Schema 和服务端都强制 `auto_apply_enabled=false`。

Consent Scope 是未来受限自动调整的授权边界，不是转账授权。即使生产版本以后启用目标自动调整，实际转账和扣款仍须通过黄色确认或红色 MFA。

## 8. API

所有接口都从 JWT 获取当前用户，不接受请求体声明用户 ID：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/family-funding/context` | 获取当前用户可用账户和正式目标 |
| POST | `/api/family-funding/negotiations` | 创建协商；要求 `Idempotency-Key` |
| GET | `/api/family-funding/negotiations` | 列出当前用户协商 |
| GET | `/api/family-funding/negotiations/{id}` | 获取协商详情 |
| GET | `/api/family-funding/negotiations/{id}/audits` | 获取协商审计 |
| PUT | `/api/family-funding/negotiations/{id}/constraints` | 补充缺失约束并继续计算 |
| POST | `/api/family-funding/negotiations/{id}/model-plans` | 提交需确定性复核的模型候选 |
| POST | `/api/family-funding/negotiations/{id}/select` | 选择方案并生成候选 |
| POST | `/api/family-funding/negotiations/{id}/confirm` | 重读快照后二次确认 |
| POST | `/api/family-funding/negotiations/{id}/reject` | 拒绝 |
| POST | `/api/family-funding/negotiations/{id}/cancel` | 取消 |
| POST | `/api/family-funding/negotiations/{id}/handoff` | 转人工 |
| POST | `/api/family-funding/negotiations/{id}/undo` | 校验后撤销目标变更 |
| GET/POST | `/api/family-funding/consent-scopes` | 查询/创建授权范围 |
| DELETE | `/api/family-funding/consent-scopes/{id}` | 立即撤销授权范围 |

## 9. 前端使用

1. 登录后切换到“资金协商”。
2. 至少勾选两个已有目标。
3. 对每个目标明确选择刚性或弹性；弹性目标填写最低金额和允许调整方式。
4. 填写不可动用备用金并点击“计算方案”。
5. 比较保证、条件和不可行方案的逐日余额、缺口与调整理由。
6. 填写选择理由并选择方案。
7. 在 `PENDING_CONFIRMATION` 状态点击“二次确认”，或拒绝、转人工。
8. 已应用的目标变更可以撤销；并发变化时系统会拒绝自动撤销。

## 10. 验收示例

输入：余额 10000 元、学费 8000 元为刚性目标、礼物 3000 元为弹性目标，礼物最低金额 1000 元且允许缩减/延期/分期。

预期至少得到：

- 保证方案：礼物缩减到 2000 元，学费 8000 元，期末保证余额为 0 元。
- 条件方案：礼物延期并等待至少 1000 元确认收入，明确标为 `CONDITIONAL`。
- 原方案：总额 11000 元、缺口 1000 元，标为 `INFEASIBLE`，不能选择。

修改账户余额后再确认旧方案，接口必须返回 409 和 `FUNDING_SNAPSHOT_CHANGED`，协商回到 `WAITING_SELECTION` 并基于新余额重新计算。

## 11. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_family_funding_planner.py `
  backend\tests\test_family_funding_negotiation.py `
  -q -p no:cacheprovider
```

测试覆盖截止日期先后、收入到账时点、备用金、两个刚性目标同时不足、预测收入、非法模型方案、候选阶段零写入、并发余额变化、跨用户隔离、撤销和 Consent Scope。
