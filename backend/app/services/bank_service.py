"""Mock Bank Sandbox 的确定性业务服务。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from uuid import uuid4

from backend.app.repositories.memory import MemoryRepository


class BankService:
    def __init__(self, repository: MemoryRepository) -> None:
        self.repo = repository

    def analyze_bill(self, user_id: str) -> dict:
        transactions = [item for item in self.repo.transactions if item["user_id"] == user_id]
        expenses = [item for item in transactions if item["amount"] < 0]
        category_totals: defaultdict[str, float] = defaultdict(float)
        for item in expenses:
            category_totals[item["category"]] += abs(item["amount"])
        total = round(sum(abs(item["amount"]) for item in expenses), 2)
        typical_expense = median(abs(item["amount"]) for item in expenses) if expenses else 0.0
        abnormal = [
            self._explain_abnormal_transaction(
                item,
                total_expense=total,
                typical_expense=typical_expense,
            )
            for item in expenses
            if item.get("is_abnormal")
        ]
        categories = [
            {"name": name, "amount": round(amount, 2), "percentage": round(amount / total * 100, 1)}
            for name, amount in sorted(category_totals.items(), key=lambda pair: pair[1], reverse=True)
        ]
        top_category = categories[0]["name"] if categories else "暂无"
        abnormal_text = (
            f"检测到 {len(abnormal)} 笔异常交易，请优先核对商户与发生时间。"
            if abnormal
            else "本月未检测到达到规则阈值的异常交易。"
        )
        subscriptions = self.list_subscriptions(user_id)
        unused = [item for item in subscriptions if item.get("last_used_days", 0) >= 60]
        subscription_text = f"检测到 {len(subscriptions)} 项周期扣费"
        if unused:
            subscription_text += f"，其中{unused[0]['name']}已 {unused[0]['last_used_days']} 天未使用。"
        else:
            subscription_text += "，暂未发现长期未使用项目。"
        return {
            "summary": {
                "expense": total,
                "income": round(sum(item["amount"] for item in transactions if item["amount"] > 0), 2),
                "currency": "CNY",
            },
            "categories": categories,
            "abnormal_transactions": abnormal,
            "insights": [
                f"{top_category}是本月占比最高的支出类别。{abnormal_text}",
                subscription_text,
            ],
        }

    @staticmethod
    def _explain_abnormal_transaction(
        transaction: dict,
        *,
        total_expense: float,
        typical_expense: float,
    ) -> dict:
        amount = abs(float(transaction["amount"]))
        expense_share = round(amount / total_expense * 100, 1) if total_expense else 0.0
        typical_multiple = (
            round(amount / typical_expense, 1) if typical_expense else None
        )
        reasons: list[str] = []
        if expense_share >= 40:
            reasons.append(f"单笔金额占本月支出的 {expense_share:.1f}%")
        if typical_multiple is not None and typical_multiple >= 3:
            reasons.append(f"金额约为本月单笔消费中位数的 {typical_multiple:.1f} 倍")
        if not reasons:
            reasons.append("命中银行沙箱的异常交易规则，请核对商户与交易时间")
        return {
            **transaction,
            "expense_share": expense_share,
            "typical_multiple": typical_multiple,
            "anomaly_reasons": reasons,
        }

    def list_subscriptions(self, user_id: str) -> list[dict]:
        return [item.copy() for item in self.repo.subscriptions if item["user_id"] == user_id]

    def find_subscription(self, user_id: str, keyword: str | None) -> dict | None:
        active = [
            item
            for item in self.repo.subscriptions
            if item["user_id"] == user_id and item["status"] != "CANCELED"
        ]
        if keyword:
            return next((item for item in active if keyword in item["name"]), None)
        return next((item for item in active if item["status"] == "SUSPICIOUS"), active[0] if active else None)

    def cancel_subscription(self, user_id: str, subscription_id: str) -> dict:
        item = next(
            item
            for item in self.repo.subscriptions
            if item["id"] == subscription_id and item["user_id"] == user_id
        )
        item["status"] = "CANCELED"
        return item.copy()

    def resolve_recipient(self, user_id: str, name: str) -> dict:
        contact = next(
            (item for item in self.repo.contacts if item["user_id"] == user_id and item["name"] == name),
            None,
        )
        if contact:
            return contact.copy()
        return {"id": f"new_{uuid4().hex[:8]}", "name": name, "account_masked": "待核验", "trusted": False}

    def create_transfer_preview(self, user_id: str, recipient: dict, amount: float, risk_hints: list[str]) -> dict:
        account = next(item for item in self.repo.accounts if item["user_id"] == user_id)
        preview = {
            "preview_id": f"preview_{uuid4().hex[:10]}",
            "user_id": user_id,
            "from_account_id": account["id"],
            "recipient": recipient,
            "amount": amount,
            "currency": "CNY",
            "fee": 0.0,
            "risk_hints": risk_hints,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "PENDING",
        }
        self.repo.previews[preview["preview_id"]] = preview
        return preview.copy()

    def execute_transfer(self, user_id: str, preview_id: str) -> dict:
        preview = self.repo.previews[preview_id]
        if preview["user_id"] != user_id:
            raise PermissionError("RESOURCE_FORBIDDEN")
        if preview["status"] != "PENDING":
            raise ValueError("REPLAY_DETECTED")
        account = next(
            item
            for item in self.repo.accounts
            if item["id"] == preview["from_account_id"] and item["user_id"] == user_id
        )
        if account["available_balance"] < preview["amount"]:
            raise ValueError("INSUFFICIENT_BALANCE")
        account["available_balance"] -= preview["amount"]
        preview["status"] = "SUCCESS"
        transaction_id = f"txn_{uuid4().hex[:12]}"
        preview["transaction_id"] = transaction_id
        self.repo.transactions.insert(
            0,
            {
                "id": transaction_id,
                "user_id": user_id,
                "account_id": account["id"],
                "merchant": preview["recipient"]["name"],
                "amount": -preview["amount"],
                "category": "转账",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"transaction_id": transaction_id, "status": "SUCCESS", "preview": preview.copy()}
