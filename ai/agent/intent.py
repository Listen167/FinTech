"""中文银行意图解析器。

当前实现保证离线演示稳定；生产版本可在此模块前增加 LLM 结构化输出，
并继续使用相同的字段校验和安全策略。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from copy import deepcopy


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
RELATION_LABELS = (
    "爱人",
    "妻子",
    "老婆",
    "丈夫",
    "老公",
    "女儿",
    "儿子",
    "孩子",
    "父亲",
    "爸爸",
    "母亲",
    "妈妈",
)


def extract_amount(message: str) -> float | None:
    patterns = (
        r"(?:转|汇|打|支付|给)[^\d]{0,12}(\d+(?:\.\d{1,2})?)\s*(?:元|块|人民币)?",
        r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|人民币)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return float(match.group(1))
    return None


def extract_recipient(message: str) -> str | None:
    patterns = (
        r"(?:转|汇|打)(?:给|向)\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})",
        r"给\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})\s*(?:转|汇|打)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1).rstrip("，。,.的")
    return None


def _extract_subscription(message: str) -> str | None:
    known_names = ("云盘", "视频", "音乐", "健身", "会员")
    return next((name for name in known_names if name in message), None)


def _today_beijing() -> date:
    return datetime.now(BEIJING_TIMEZONE).date()


def extract_related_person(message: str) -> str | None:
    label = next((item for item in RELATION_LABELS if item in message), None)
    if label in {"妻子", "老婆", "丈夫", "老公"}:
        return "爱人"
    return label


def relation_type_for(label: str | None) -> str | None:
    if label == "爱人":
        return "SPOUSE_OF"
    if label in {"女儿", "儿子", "孩子"}:
        return "PARENT_OF"
    if label in {"父亲", "爸爸", "母亲", "妈妈"}:
        return "CHILD_OF"
    return None


def _event_type(message: str) -> str | None:
    if "生日" in message:
        return "BIRTHDAY"
    if "学费" in message:
        return "TUITION_DUE"
    if "纪念日" in message:
        return "ANNIVERSARY"
    return None


def _safe_event_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_event_date_parts(
    message: str, *, today: date | None = None
) -> tuple[str | None, int | None, int | None]:
    reference = today or _today_beijing()
    full = re.search(
        r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月(?:\s*(\d{1,2})\s*(?:日|号))?",
        message,
    )
    if full:
        explicit_year, month_text, day_text = full.groups()
        month = int(month_text)
        year = int(explicit_year) if explicit_year else reference.year
        if not explicit_year and month < reference.month:
            year += 1
        if day_text:
            event_date = _safe_event_date(year, month, int(day_text))
            if not explicit_year and event_date and date.fromisoformat(event_date) < reference:
                year += 1
                event_date = _safe_event_date(year, month, int(day_text))
            return event_date, year, month
        return None, year, month

    next_month = re.search(r"下个?月(?:\s*(\d{1,2})\s*(?:日|号))?", message)
    if next_month:
        month = reference.month % 12 + 1
        year = reference.year + (1 if reference.month == 12 else 0)
        if next_month.group(1):
            return _safe_event_date(year, month, int(next_month.group(1))), year, month
        return None, year, month

    return None, None, None


def _extract_day(message: str) -> int | None:
    match = re.search(r"(?<!月)(\d{1,2})\s*(?:日|号)", message)
    return int(match.group(1)) if match else None


def extract_reminder_days(message: str) -> int | None:
    match = re.search(r"提前\s*(\d{1,3})\s*天", message)
    if match:
        return int(match.group(1))
    chinese_numbers = {"一": 1, "两": 2, "二": 2, "三": 3, "七": 7}
    match = re.search(r"提前\s*([一两二三七])\s*天", message)
    return chinese_numbers.get(match.group(1)) if match else None


def _life_missing_slots(slots: dict) -> list[str]:
    missing = [
        field
        for field in ("related_person", "relation_type", "event_type", "event_date")
        if not slots.get(field)
    ]
    if slots.get("goal_required") and slots.get("target_amount") is None:
        missing.append("target_amount")
    return missing


def _parse_life_event(message: str, hints: list[str]) -> dict | None:
    event_type = _event_type(message)
    if event_type is None:
        return None

    related_person = extract_related_person(message)
    event_date, event_year, event_month = extract_event_date_parts(message)
    amount = extract_amount(message)
    goal_keywords = ("留", "存", "准备", "预留", "攒", "预算", "礼物", "交学费", "缴学费")
    goal_required = event_type == "TUITION_DUE" or amount is not None or any(
        word in message for word in goal_keywords
    )
    event_names = {
        "BIRTHDAY": "生日",
        "TUITION_DUE": "学费缴纳",
        "ANNIVERSARY": "纪念日",
    }
    person_label = related_person or "关系人"
    slots = {
        "related_person": related_person,
        "relation_type": relation_type_for(related_person),
        "event_type": event_type,
        "event_label": f"{person_label}{event_names[event_type]}",
        "event_date": event_date,
        "event_year": event_year,
        "event_month": event_month,
        "recurrence_rule": "FREQ=YEARLY" if event_type in {"BIRTHDAY", "ANNIVERSARY"} else None,
        "goal_required": goal_required,
        "goal_label": (
            f"{person_label}生日礼物心愿金"
            if event_type == "BIRTHDAY"
            else f"{person_label}{event_names[event_type]}目标"
        ),
        "target_amount": amount if goal_required else None,
        "currency": "CNY",
        "reminder_days_before": extract_reminder_days(message),
    }
    return {
        "name": "life.event_plan",
        "confidence": 0.94,
        "slots": slots,
        "missing_slots": _life_missing_slots(slots),
        "risk_hints": hints,
    }


def parse_intent(message: str, risk_hints: list[str] | None = None) -> dict:
    text = message.strip()
    hints = list(risk_hints or [])
    has_bill = any(word in text for word in ("账单", "消费", "花了", "花在哪", "异常交易", "财务体检"))
    has_subscription = any(word in text for word in ("订阅", "会员", "续费", "代扣"))
    wants_cancel = any(word in text for word in ("取消", "停掉", "关闭", "不用的", "没用的"))
    has_transfer = any(word in text for word in ("转账", "转给", "汇给", "打给", "安全账户")) or bool(
        re.search(r"给.{1,12}(转|汇|打)", text)
    )

    if sum((has_bill, has_subscription, has_transfer)) > 1:
        return {
            "name": "finance.cross_scenario",
            "confidence": 0.96,
            "slots": {
                "include_bill": has_bill,
                "include_subscriptions": has_subscription,
                "cancel_subscription": has_subscription and wants_cancel,
                "include_transfer": has_transfer,
                "subscription_keyword": _extract_subscription(text),
                "recipient": extract_recipient(text),
                "amount": extract_amount(text),
            },
            "missing_slots": [],
            "risk_hints": hints,
        }

    life_event = _parse_life_event(text, hints)
    if life_event is not None and not has_transfer:
        return life_event

    if has_transfer:
        amount = extract_amount(text)
        recipient = extract_recipient(text)
        missing = []
        if amount is None:
            missing.append("amount")
        if recipient is None:
            missing.append("recipient")
        return {
            "name": "transfer.create",
            "confidence": 0.95,
            "slots": {"amount": amount, "recipient": recipient, "currency": "CNY"},
            "missing_slots": missing,
            "risk_hints": hints,
        }

    if has_subscription:
        return {
            "name": "subscription.cancel" if wants_cancel else "subscription.list",
            "confidence": 0.93,
            "slots": {"keyword": _extract_subscription(text)},
            "missing_slots": [],
            "risk_hints": hints,
        }

    if has_bill:
        return {
            "name": "bill.analyze",
            "confidence": 0.94,
            "slots": {"period": "current_month", "detect_abnormal": True},
            "missing_slots": [],
            "risk_hints": hints,
        }

    return {
        "name": "unknown",
        "confidence": 0.35,
        "slots": {},
        "missing_slots": [],
        "risk_hints": hints,
    }


def is_cancel_follow_up(message: str) -> bool:
    normalized = re.sub(r"[\s，。,.！!？?]", "", message)
    return normalized in {"算了", "取消", "不办了", "不用了", "停止", "退出"}


def is_bill_result_follow_up(message: str) -> bool:
    """识别需要引用上一轮账单结果的简短追问。"""

    normalized = re.sub(r"[\s，。,.！!？?]", "", message)
    exact_phrases = {
        "怎么说",
        "什么意思",
        "哪一笔",
        "是哪一笔",
        "为什么",
        "怎么回事",
        "详细说说",
        "展开说说",
        "怎么处理",
        "该怎么办",
    }
    if normalized in exact_phrases:
        return True
    return any(
        phrase in normalized
        for phrase in ("为什么异常", "异常原因", "具体明细", "交易明细", "有多异常")
    )


def merge_follow_up(
    pending_intent: dict,
    message: str,
    current_intent: dict,
    last_recipient: str | None = None,
) -> tuple[dict, list[str]]:
    """将短回答合并到等待补充的意图，不覆盖已有明确字段。"""

    if pending_intent.get("name") == "life.event_plan":
        return _merge_life_follow_up(pending_intent, message, current_intent)

    if pending_intent.get("name") != "transfer.create":
        return current_intent, []
    if current_intent.get("name") not in {"unknown", "transfer.create"}:
        return current_intent, []

    merged = deepcopy(pending_intent)
    slots = merged.setdefault("slots", {})
    resolved: list[str] = []
    amount = extract_amount(message)
    recipient = extract_recipient(message)

    if amount is None and current_intent.get("name") == "transfer.create":
        amount = current_intent.get("slots", {}).get("amount")
    if recipient is None and current_intent.get("name") == "transfer.create":
        recipient = current_intent.get("slots", {}).get("recipient")
    if recipient is None and any(token in message for token in ("刚才那个人", "刚才的人", "他", "她", "TA")):
        recipient = slots.get("recipient") or last_recipient

    if amount is not None:
        slots["amount"] = amount
        resolved.append("amount")
    if recipient:
        slots["recipient"] = recipient
        resolved.append("recipient")

    merged["missing_slots"] = [field for field in ("recipient", "amount") if slots.get(field) is None]
    merged["confidence"] = max(float(merged.get("confidence", 0)), float(current_intent.get("confidence", 0)))
    merged["risk_hints"] = sorted(
        set(merged.get("risk_hints", [])) | set(current_intent.get("risk_hints", []))
    )
    merged["parser"] = current_intent.get("parser", merged.get("parser", "deterministic_fallback"))
    if current_intent.get("model"):
        merged["model"] = current_intent["model"]
    return merged, sorted(set(resolved))


def _merge_life_follow_up(
    pending_intent: dict,
    message: str,
    current_intent: dict,
) -> tuple[dict, list[str]]:
    if current_intent.get("name") not in {"unknown", "life.event_plan"}:
        return current_intent, []

    merged = deepcopy(pending_intent)
    slots = merged.setdefault("slots", {})
    previous_missing = set(merged.get("missing_slots", []))
    current_slots = current_intent.get("slots", {})
    for field in (
        "related_person",
        "relation_type",
        "event_type",
        "event_label",
        "event_date",
        "event_year",
        "event_month",
        "recurrence_rule",
        "goal_required",
        "goal_label",
        "target_amount",
        "currency",
        "reminder_days_before",
    ):
        if current_slots.get(field) is not None:
            slots[field] = current_slots[field]

    related_person = extract_related_person(message)
    if related_person:
        slots["related_person"] = related_person
        slots["relation_type"] = relation_type_for(related_person)
        event_names = {
            "BIRTHDAY": "生日",
            "TUITION_DUE": "学费缴纳",
            "ANNIVERSARY": "纪念日",
        }
        event_name = event_names.get(slots.get("event_type"))
        if event_name:
            slots["event_label"] = f"{related_person}{event_name}"
            slots["goal_label"] = (
                f"{related_person}生日礼物心愿金"
                if slots.get("event_type") == "BIRTHDAY"
                else f"{related_person}{event_name}目标"
            )

    event_date, event_year, event_month = extract_event_date_parts(message)
    if event_date:
        slots["event_date"] = event_date
    if event_year:
        slots["event_year"] = event_year
    if event_month:
        slots["event_month"] = event_month
    if not slots.get("event_date") and slots.get("event_year") and slots.get("event_month"):
        day = _extract_day(message)
        if day:
            slots["event_date"] = _safe_event_date(
                int(slots["event_year"]), int(slots["event_month"]), day
            )

    amount = extract_amount(message)
    if amount is not None:
        slots["target_amount"] = amount
    reminder_days = extract_reminder_days(message)
    if reminder_days is not None:
        slots["reminder_days_before"] = reminder_days

    merged["missing_slots"] = _life_missing_slots(slots)
    resolved = sorted(previous_missing - set(merged["missing_slots"]))
    merged["confidence"] = max(
        float(merged.get("confidence", 0)), float(current_intent.get("confidence", 0))
    )
    merged["risk_hints"] = sorted(
        set(merged.get("risk_hints", [])) | set(current_intent.get("risk_hints", []))
    )
    merged["parser"] = current_intent.get(
        "parser", merged.get("parser", "deterministic_fallback")
    )
    if current_intent.get("model"):
        merged["model"] = current_intent["model"]
    return merged, resolved
