"""Agent 能力层的统一入口。

这里只负责输入安全、意图和计划，不直接修改任何银行业务状态。
"""

from ai.agent.intent import (
    is_bill_result_follow_up,
    is_cancel_follow_up,
    merge_follow_up,
    parse_intent,
)
from ai.agent.llm_parser import parse_with_llm
from ai.agent.planner import build_plan
from ai.safety.guard import evaluate_text


def handle_message(session_id: str, message: str, conversation_context: dict | None = None) -> dict:
    safety = evaluate_text(message)
    if safety["blocked"]:
        return {
            "session_id": session_id,
            "type": "BLOCKED",
            "message": "检测到试图绕过安全规则的指令，本次操作已停止。",
            "safety": safety,
        }

    context = conversation_context or {}
    if context.get("pending_intent") and is_cancel_follow_up(message):
        return {
            "session_id": session_id,
            "type": "CANCELLED",
            "message": "已取消当前待补充的任务，不会执行任何资金操作。",
            "safety": safety,
            "model_trace": None,
        }

    fallback_intent = parse_intent(message, safety["risk_hints"])
    llm_intent, model_trace = parse_with_llm(message, safety["risk_hints"], context)
    intent = llm_intent or fallback_intent
    if (
        intent["name"] == "unknown"
        and context.get("last_completed_intent") == "bill.analyze"
        and is_bill_result_follow_up(message)
    ):
        intent = {
            "name": "bill.analyze",
            "confidence": 0.96,
            "slots": {"period": "current_month", "detect_abnormal": True},
            "missing_slots": [],
            "risk_hints": safety["risk_hints"],
            "parser": "contextual_follow_up",
        }
    intent.setdefault("parser", "deterministic_fallback")
    resolved_slots: list[str] = []
    if context.get("pending_intent"):
        intent, resolved_slots = merge_follow_up(
            context["pending_intent"],
            message,
            intent,
            context.get("last_recipient"),
        )
    if intent["name"] == "transfer.create":
        fallback_slots = fallback_intent.get("slots", {})
        intent.setdefault("slots", {})
        for field in ("recipient", "amount", "currency"):
            if intent["slots"].get(field) is None and fallback_slots.get(field) is not None:
                intent["slots"][field] = fallback_slots[field]
        intent["missing_slots"] = [field for field in ("recipient", "amount") if intent["slots"].get(field) is None]
    elif intent["name"] == "life.event_plan":
        fallback_slots = fallback_intent.get("slots", {})
        intent.setdefault("slots", {})
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
            if intent["slots"].get(field) is None and fallback_slots.get(field) is not None:
                intent["slots"][field] = fallback_slots[field]
        intent["missing_slots"] = [
            field
            for field in ("related_person", "relation_type", "event_type", "event_date")
            if not intent["slots"].get(field)
        ]
        if intent["slots"].get("goal_required") and intent["slots"].get("target_amount") is None:
            intent["missing_slots"].append("target_amount")
    plan = build_plan(intent)
    return {
        "session_id": session_id,
        "type": "PLAN",
        "message": "已完成意图识别与任务规划，等待后端策略引擎执行。",
        "safety": safety,
        "intent": intent,
        "plan": plan,
        "model_trace": model_trace,
        "resolved_slots": resolved_slots,
    }

