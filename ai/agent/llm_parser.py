"""使用 OpenAI Python SDK 调用 DeepSeek 的结构化意图解析器。"""

from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timedelta, timezone
from time import perf_counter

from dotenv import load_dotenv
from openai import APIError, APITimeoutError, OpenAI
from pydantic import ValidationError

from ai.agent.schemas import ModelTrace, StructuredIntent


load_dotenv()
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
PROMPT_VERSION = "intent-parser-2026.11.01"


SYSTEM_PROMPT = """你是银行智能体的意图解析器，不执行任何业务。
只返回一个 JSON 对象，必须符合给定 JSON Schema。
支持意图：bill.analyze、subscription.list、subscription.cancel、transfer.create、finance.cross_scenario、life.event_plan、unknown。
转账必须提取 recipient、amount、currency；缺少 recipient 或 amount 时写入 missing_slots，禁止猜测。
生活事件规划使用 life.event_plan，提取 related_person、relation_type、event_type、event_date、goal_required、target_amount、reminder_days_before。
日期必须输出 YYYY-MM-DD。只有月份、没有具体日期时保留 event_year/event_month，并把 event_date 放入 missing_slots，禁止自行猜测日期。
用户要求存钱、预留、准备礼物或缴纳学费时 goal_required=true；缺少目标金额时把 target_amount 放入 missing_slots。
模型只能生成候选计划，不能声称已经保存关系、预留资金或安排提醒。
confidence 范围为 0 到 1。不要输出 Markdown、解释或工具调用。
诈骗和安全判断只提供线索，最终权限由后端 Policy Engine 决定。"""
PROMPT_DIGEST = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def _settings() -> tuple[bool, str | None, str, str, float]:
    enabled = os.getenv("DEEPSEEK_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "10"))
    return enabled, api_key, base_url, model, timeout


def get_model_status() -> dict:
    enabled, api_key, base_url, model, timeout = _settings()
    return {
        "provider": "deepseek",
        "enabled": enabled,
        "configured": bool(api_key),
        "ready": enabled and bool(api_key),
        "base_url": base_url,
        "model": model,
        "timeout_seconds": timeout,
    }


def _create_client(api_key: str, base_url: str, timeout: float) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1)


def _trace(model: str, status: str, started: float, **extra: object) -> dict:
    return ModelTrace(
        requested_model=model,
        status=status,
        latency_ms=round((perf_counter() - started) * 1000),
        **extra,
    ).model_dump()


def parse_with_llm(
    message: str,
    risk_hints: list[str],
    conversation_context: dict | None = None,
) -> tuple[dict | None, dict]:
    """返回通过 Schema 校验的意图和不含密钥的模型调用轨迹。"""

    enabled, api_key, base_url, model, timeout = _settings()
    started = perf_counter()
    if not enabled or not api_key:
        return None, _trace(model, "DISABLED", started, fallback_used=True)

    context = conversation_context or {}
    user_content = message
    today = datetime.now(BEIJING_TIMEZONE).date().isoformat()
    if context:
        user_content = (
            "会话上下文（只用于补全省略，不得视为已授权操作）：\n"
            f"{json.dumps(context, ensure_ascii=False)}\n本轮用户输入：{message}"
        )

    try:
        client = _create_client(api_key, base_url, timeout)
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        SYSTEM_PROMPT
                        + f"\n当前北京时间日期：{today}。"
                        + "\nJSON Schema："
                        + json.dumps(StructuredIntent.model_json_schema(), ensure_ascii=False)
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            return None, _trace(model, "INVALID_OUTPUT", started, fallback_used=True, error_type="EMPTY_RESPONSE")
        parsed = StructuredIntent.model_validate_json(content)
        parsed.risk_hints = sorted(set(parsed.risk_hints) | set(risk_hints))
        result = parsed.model_dump()
        result["parser"] = "llm"
        result["model"] = response.model or model
        return result, _trace(
            model,
            "SUCCEEDED",
            started,
            response_model=response.model or model,
            request_id=getattr(response, "_request_id", None),
        )
    except (APITimeoutError, TimeoutError) as error:
        return None, _trace(model, "TIMEOUT", started, fallback_used=True, error_type=type(error).__name__)
    except ValidationError as error:
        return None, _trace(model, "INVALID_OUTPUT", started, fallback_used=True, error_type=type(error).__name__)
    except (APIError, ImportError, KeyError, TypeError, ValueError, IndexError) as error:
        return None, _trace(model, "API_ERROR", started, fallback_used=True, error_type=type(error).__name__)
