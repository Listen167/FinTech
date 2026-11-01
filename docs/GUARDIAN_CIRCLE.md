# 家庭守护授权圈设计与实现

## 1. 设计目标

家庭守护圈解决的不是“让家人替我操作账户”，而是“在高风险时让指定家人获得最少且必要的风险信息”。系统在家庭协助与个人隐私之间建立一条明确边界：

- 生活图谱中的丈夫、妻子、父亲、女儿等关系只是生活事实，不产生权限。
- 守护关系必须由本人创建一次性邀请，并由指定账号登录后接受。
- 守护人默认只能看风险摘要，不能看完整账单、余额、交易明细或原始账号。
- 守护人的确认只表示“风险已知”，不能替代本人 MFA，也不能授权或执行资金操作。
- 本人或守护人均可即时撤销；撤销和到期后，旧链接及待确认请求立即失效。

## 2. 核心状态机

### 2.1 一次性邀请

```text
PENDING
  -> ACCEPTED
  -> REJECTED
  -> CANCELED
  -> EXPIRED
```

邀请令牌使用安全随机数生成，API 只在创建时返回一次，数据库仅保存 SHA-256 摘要。兑换时同时校验登录用户、令牌状态和时间边界，不能转交给第三人或重复使用。

### 2.2 Consent Scope

```text
ACTIVE -> REVOKED
ACTIVE -> EXPIRED
```

Consent 明确包含：

| 维度 | 当前实现 |
| --- | --- |
| 通知场景 | 大额转账、新收款人、疑似诈骗 |
| 金额范围 | 单次金额下限和上限 |
| 可见字段 | 风险等级、场景、金额区间、脱敏收款人、风险摘要、发生时间 |
| 有效期 | 最长一年，服务端按 UTC 存储、前端按北京时间展示 |
| 固定禁止项 | 完整账单、账户余额、交易明细、原始账号、MFA、资金执行 |

同一用户与同一守护人在同一时刻最多存在一个活动 Consent。唯一的 `active_pair_key` 在数据库层阻止并发重复授权。

### 2.3 风险双确认

```text
AWAITING_OWNER
  -> AWAITING_GUARDIAN
      -> ACKNOWLEDGED
      -> REJECTED
  -> REJECTED
  -> EXPIRED / CANCELED
```

本人必须先确认愿意把当前风险摘要交给守护人，守护人才可以确认“风险已知”。任意终态都固定返回：

```json
{
  "risk_acknowledgement_only": true,
  "funds_executed": false,
  "mfa_satisfied": false,
  "execution_authorized": false
}
```

该状态机不会修改 Agent 原有黄色确认、红色 MFA 或反诈冷静期。即使守护人确认风险已知，资金操作仍必须由本人在受信 App 内重新完成原有授权链路。

## 3. 数据最小化

守护人响应由后端按 Consent 的 `visible_fields` 白名单重新构造，不直接返回风险记录行：

- 金额转换为“1000 元以下”“1000 至 4999 元”等区间。
- 收款人字段必须预先脱敏，连续六位以上数字会被拒绝。
- 风险摘要中的连续敏感数字会再次替换为 `****`。
- 守护人响应不返回 `source_run_id`，也没有账户、余额、流水字段。
- Consent 撤销或到期后，守护人列表和详情接口立即拒绝访问。

## 4. 数据库

迁移文件：

```text
backend/migrations/versions/20261101_11_guardian_circle.py
```

新增表：

| 表 | 用途 |
| --- | --- |
| `guardian_invitations` | 一次性邀请摘要、双方身份、候选范围与状态 |
| `guardian_consents` | 生效范围、有效期、撤销人和撤销原因 |
| `guardian_risk_reviews` | 风险双确认状态和最小可见载荷 |
| `guardian_audits` | 邀请、接受、撤销、确认、拒绝和到期审计 |

执行：

```powershell
python -m alembic upgrade head
python -m alembic current
```

当前版本应为 `20261101_11 (head)`。

## 5. API

所有接口都要求当前用户 JWT，服务端不接受客户端传入“当前用户 ID”。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/guardian-circle/invitations` | 本人创建一次性邀请 |
| `GET` | `/api/guardian-circle/invitations` | 查看与当前账号有关的邀请 |
| `POST` | `/api/guardian-circle/invitations/accept` | 指定守护账号接受邀请 |
| `POST` | `/api/guardian-circle/invitations/reject` | 指定守护账号拒绝邀请 |
| `POST` | `/api/guardian-circle/invitations/{id}/cancel` | 邀请人取消待处理邀请 |
| `GET` | `/api/guardian-circle/consents` | 查看当前可见授权 |
| `POST` | `/api/guardian-circle/consents/{id}/revoke` | 任一方即时撤销 |
| `GET` | `/api/guardian-circle/consents/{id}/audits` | 查看授权审计链路 |
| `POST` | `/api/guardian-circle/risk-reviews` | 本人建立风险双确认 |
| `GET` | `/api/guardian-circle/risk-reviews` | 查看当前仍有权访问的风险摘要 |
| `POST` | `/api/guardian-circle/risk-reviews/{id}/owner-decision` | 本人先确认或拒绝 |
| `POST` | `/api/guardian-circle/risk-reviews/{id}/guardian-decision` | 守护人确认风险已知或建议停止 |

## 6. 前端演示

1. 使用 `lin_an` 登录，切换到“守护圈”。
2. 指定 `user_002`，选择场景、金额上限、可见字段和截止日期，生成一次性令牌。
3. 退出后使用 `zhou_ning` 登录，切换到“守护圈”，粘贴令牌并接受。
4. 回到 `lin_an`，创建一条新收款人或疑似诈骗风险摘要，并由本人先确认发送。
5. 切换到 `zhou_ning`，只看到金额区间和授权字段，点击“确认风险已知”。
6. 展示结果中的“不满足 MFA、不授权执行”，再即时撤销 Consent，证明旧摘要立即不可访问。

## 7. 测试

```powershell
python -m pytest backend/tests/test_guardian_circle.py -q
```

当前测试覆盖：双方确认、令牌只存摘要、第三人越权、令牌重放、字段白名单、金额区间、本人先确认、守护人不能授权资金、范围越权、敏感账号拒绝、即时撤销、旧访问失效、邀请和 Consent 到期以及 API 稳定错误码。

## 8. 生产化边界

比赛版使用两个演示银行用户和站内令牌交换。生产环境还需要接入银行统一身份、设备绑定、可信联系人核验、邀请通知投递、隐私授权留痕、风控案件系统和独立审计存储。当前实现不能连接真实资金，也不能把家庭守护确认描述为法定代理或账户共同控制。
