# 操作护照说明

## 1. 功能定位

操作护照不是模型生成的总结，而是后端从 Run、结构化意图、任务图、策略结果、用户授权记录、工具回执和审计日志中实时重建的脱敏证据视图。它回答以下问题：

- 用户原始请求是什么，模型将其理解成什么意图；
- 本次运行使用了哪个模型、Prompt、Planner、Executor 和 Policy 版本；
- 为什么得到当前风险等级，用户通过何种方式授权；
- 银行沙箱是否真正返回了对应工具回执；
- 审计记录是否与生成摘要时的内容一致。

护照不会代替银行账本，也不会因为模型回复了“成功”就声明操作成功。

## 2. 三层证据

护照固定区分三类证据：

1. `MODEL_INTERPRETATION`：结构化意图、解析器、置信度和模型调用轨迹。它只证明系统如何理解请求。
2. `USER_AUTHORIZATION`：黄色确认、红色 MFA 或绿色只读免确认。授权只覆盖绑定的操作快照。
3. `BANK_SANDBOX_RESULT`：确定性任务图中目标工具的 `TOOL_RESULT` 回执。缺少最终工具回执时，`success_claim_allowed` 必须为 `false`。

转账必须存在 `transfer.execute` 回执，取消订阅必须存在 `subscription.cancel.execute` 回执。查询、预览或模型文本不能替代最终回执。

## 3. 接口

两个接口都必须携带当前用户的 JWT Access Token，并校验 Run 所有权：

```text
GET /api/operation-passports/runs/{run_id}
GET /api/operation-passports/runs/{run_id}/export
```

第二个接口返回同一份脱敏 JSON，响应包含 `Content-Disposition: attachment` 与 `Cache-Control: no-store`。跨用户读取返回稳定错误 `RESOURCE_FORBIDDEN`。

## 4. 隐私与脱敏

护照在输出边界递归脱敏，不修改仓库中的业务事实。默认规则包括：

- 密码、OTP、验证码、Access Token、Refresh Token、CSRF Token、API Key 和 Secret 替换为 `[REDACTED]`；
- 手机号只保留前三位和后四位；
- 账户类标识仅保留首尾片段；
- Bearer 凭据和 JWT 形态字符串直接隐藏；
- 前端只显示摘要截断值，完整摘要仅保留在脱敏 JSON 中用于复核。

## 5. 摘要链边界

每个 Run 的新审计事件记录 `chain_index`、`previous_digest` 和 `record_digest`。护照会重新计算整条链，结果可能为：

- `VERIFIED`：当前记录与摘要链一致；
- `TAMPERED`：检测到内容、顺序或前序摘要异常；
- `LEGACY_UNVERIFIED`：历史记录没有摘要链；
- `PARTIAL_UNVERIFIED`：同一 Run 同时包含新旧记录；
- `EMPTY`：没有审计事件。

比赛版摘要链只能检测记录生成后的异常修改。由于未接入外部可信时间戳、数字签名或不可变存储，项目不宣称记录绝对不可篡改。

## 6. 前端使用

任务生成后，右侧“操作护照”区域显示完整性与银行回执摘要。点击“查看护照”打开证据面板，可查看请求版本、三层证据、风险和授权快照、工具回执及北京时间线；下载按钮导出脱敏 JSON。手机端使用全屏面板，不显示完整账户、验证码或 Token。

## 7. 自动化验收

专项测试位于 `backend/tests/test_operation_passport.py`，覆盖黄色确认、红色 MFA、待授权、回执缺失、审计修改检测、跨用户隔离、数据库重载和导出响应。运行：

```powershell
conda run -n fintech python -m pytest backend/tests/test_operation_passport.py -q -p no:cacheprovider
```
