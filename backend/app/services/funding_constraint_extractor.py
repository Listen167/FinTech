"""使用 DeepSeek 提取资金约束；模型输出始终经过 Pydantic 校验。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from openai import APIError, APITimeoutError, OpenAI
from pydantic import ValidationError

from backend.app.schemas.family_funding import FundingPlanningRequest


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
PROMPT_VERSION = "family-funding-extractor/1.0"
SYSTEM_PROMPT = """你是银行家庭资金约束提取器，不执行任何业务，也不替用户决定价值优先级。
只返回符合 JSON Schema 的 JSON 对象。人物关系只写入 relationship_context，不得据此生成 priority_rank。
USER_CONFIRMED 仅用于用户在本轮明确说出的金额、日期和偏好；BANK_CONFIRMED 仅用于上下文中明确标记为银行数据的字段。
历史推断使用 MODEL_INFERRED，预计收入使用 FORECAST。不得把 MODEL_INFERRED 或 FORECAST 写成已确认收入。
刚性目标 minimum_amount 必须等于 target_amount。信息缺失时不要猜测，返回 null，由系统追问。
不要输出 Markdown、解释、授权结论或已执行声明。"""
PROMPT_DIGEST = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


class FundingConstraintExtractor:
    def __init__(self, client_factory=None) -> None:
        self.client_factory = client_factory or self._create_client

    def extract(
        self,
        *,
        request_text: str,
        trusted_context: dict[str, Any],
    ) -> tuple[FundingPlanningRequest | None, list[str], dict[str, Any]]:
        enabled = os.getenv("DEEPSEEK_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "10"))
        started = perf_counter()
        if not enabled or not api_key:
            return None, ["planning"], self._trace(
                model, "DISABLED", started, fallback_used=True, error_code="MODEL_NOT_CONFIGURED"
            )
        try:
            client = self.client_factory(api_key, base_url, timeout)
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            SYSTEM_PROMPT
                            + f"\n当前北京时间日期：{datetime.now(BEIJING_TIMEZONE).date().isoformat()}。"
                            + "\nJSON Schema："
                            + json.dumps(FundingPlanningRequest.model_json_schema(), ensure_ascii=False)
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "可信上下文（仅字段标注为银行确认时才可作为 BANK_CONFIRMED）：\n"
                            + json.dumps(trusted_context, ensure_ascii=False)
                            + "\n用户请求："
                            + request_text
                        ),
                    },
                ],
            )
            content = response.choices[0].message.content
            if not content:
                return None, ["planning"], self._trace(
                    model, "INVALID_OUTPUT", started, fallback_used=True, error_code="EMPTY_RESPONSE"
                )
            planning = FundingPlanningRequest.model_validate_json(content)
            return planning, [], self._trace(
                model,
                "SUCCEEDED",
                started,
                response_model=response.model or model,
                request_id=getattr(response, "_request_id", None),
            )
        except (APITimeoutError, TimeoutError) as error:
            return None, ["planning"], self._trace(
                model, "TIMEOUT", started, fallback_used=True, error_code=type(error).__name__
            )
        except ValidationError as error:
            fields = [".".join(str(part) for part in item["loc"]) for item in error.errors()]
            return None, sorted(set(fields)) or ["planning"], self._trace(
                model, "INVALID_OUTPUT", started, fallback_used=True, error_code="SCHEMA_VALIDATION_FAILED"
            )
        except (APIError, KeyError, TypeError, ValueError, IndexError) as error:
            return None, ["planning"], self._trace(
                model, "API_ERROR", started, fallback_used=True, error_code=type(error).__name__
            )

    @staticmethod
    def _create_client(api_key: str, base_url: str, timeout: float) -> OpenAI:
        return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1)

    @staticmethod
    def _trace(model: str, status: str, started: float, **extra: Any) -> dict[str, Any]:
        return {
            "provider": "deepseek",
            "requested_model": model,
            "status": status,
            "latency_ms": round((perf_counter() - started) * 1000),
            "fallback_used": status != "SUCCEEDED",
            "prompt_version": PROMPT_VERSION,
            "prompt_digest": PROMPT_DIGEST,
            **{key: value for key, value in extra.items() if value is not None},
        }
