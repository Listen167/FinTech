"""可重复重置的银行沙箱数据。所有名称和交易均为虚构。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


DEMO_USERS = [
    {
        "id": "user_001",
        "display_name": "林安",
        "phone_masked": "138****1024",
        "role": "CUSTOMER",
        "status": "ACTIVE",
        "risk_level": "LOW",
    },
    {
        "id": "user_002",
        "display_name": "周宁",
        "phone_masked": "139****2048",
        "role": "CUSTOMER",
        "status": "ACTIVE",
        "risk_level": "LOW",
    },
]

# 保留旧名称，避免尚未迁移的演示代码失效。
DEMO_USER = DEMO_USERS[0]

DEMO_ACCOUNTS = [
    {
        "id": "acct_001",
        "user_id": "user_001",
        "name": "安心借记卡",
        "account_no_masked": "6225 **** **** 1024",
        "type": "DEBIT",
        "currency": "CNY",
        "available_balance": 52860.42,
        "frozen_balance": 0.0,
    },
    {
        "id": "acct_002",
        "user_id": "user_002",
        "name": "守护借记卡",
        "account_no_masked": "6217 **** **** 2048",
        "type": "DEBIT",
        "currency": "CNY",
        "available_balance": 18620.00,
        "frozen_balance": 0.0,
    },
]

DEMO_CONTACTS = [
    {"id": "contact_001", "user_id": "user_001", "name": "小王", "account_masked": "**** 6628", "trusted": True},
    {"id": "contact_002", "user_id": "user_001", "name": "女儿", "account_masked": "**** 1106", "trusted": True},
    {"id": "contact_003", "user_id": "user_002", "name": "小陈", "account_masked": "**** 3088", "trusted": True},
]

DEMO_SUBSCRIPTIONS = [
    {
        "id": "sub_001",
        "user_id": "user_001",
        "name": "云盘空间会员",
        "amount": 25.0,
        "currency": "CNY",
        "cycle": "MONTHLY",
        "next_charge_date": "2026-11-05",
        "status": "SUSPICIOUS",
        "last_used_days": 94,
    },
    {
        "id": "sub_002",
        "user_id": "user_001",
        "name": "星河音乐",
        "amount": 15.0,
        "currency": "CNY",
        "cycle": "MONTHLY",
        "next_charge_date": "2026-11-12",
        "status": "ACTIVE",
        "last_used_days": 2,
    },
    {
        "id": "sub_003",
        "user_id": "user_001",
        "name": "光影视频会员",
        "amount": 30.0,
        "currency": "CNY",
        "cycle": "MONTHLY",
        "next_charge_date": "2026-11-18",
        "status": "ACTIVE",
        "last_used_days": 7,
    },
    {
        "id": "sub_004",
        "user_id": "user_002",
        "name": "晨光阅读",
        "amount": 12.0,
        "currency": "CNY",
        "cycle": "MONTHLY",
        "next_charge_date": "2026-11-20",
        "status": "ACTIVE",
        "last_used_days": 3,
    },
]


def build_demo_transactions(now: datetime | None = None) -> list[dict]:
    anchor = now or datetime.now(timezone.utc)

    def occurred(days_ago: int, hour: int = 12) -> str:
        value = anchor - timedelta(days=days_ago)
        return value.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

    return [
        {"id": "txn_001", "user_id": "user_001", "account_id": "acct_001", "merchant": "城市超市", "amount": -286.30, "category": "日用", "occurred_at": occurred(2)},
        {"id": "txn_002", "user_id": "user_001", "account_id": "acct_001", "merchant": "地铁出行", "amount": -42.00, "category": "交通", "occurred_at": occurred(3)},
        {"id": "txn_003", "user_id": "user_001", "account_id": "acct_001", "merchant": "云盘空间会员", "amount": -25.00, "category": "订阅", "occurred_at": occurred(4)},
        {"id": "txn_004", "user_id": "user_001", "account_id": "acct_001", "merchant": "星河音乐", "amount": -15.00, "category": "订阅", "occurred_at": occurred(5)},
        {"id": "txn_005", "user_id": "user_001", "account_id": "acct_001", "merchant": "社区餐饮", "amount": -168.00, "category": "餐饮", "occurred_at": occurred(6)},
        {"id": "txn_006", "user_id": "user_001", "account_id": "acct_001", "merchant": "陌生数码商户", "amount": -2999.00, "category": "购物", "occurred_at": occurred(1, 2), "is_abnormal": True},
        {"id": "txn_007", "user_id": "user_001", "account_id": "acct_001", "merchant": "工资", "amount": 8500.00, "category": "收入", "occurred_at": occurred(10)},
        {"id": "txn_101", "user_id": "user_002", "account_id": "acct_002", "merchant": "社区书店", "amount": -86.00, "category": "教育", "occurred_at": occurred(2)},
        {"id": "txn_102", "user_id": "user_002", "account_id": "acct_002", "merchant": "退休金", "amount": 4200.00, "category": "收入", "occurred_at": occurred(12)},
    ]

