"""飞书消息到 Channel Gateway 的纯适配层。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import Executor, ThreadPoolExecutor
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from backend.app.errors import ApplicationError
from backend.app.schemas.channel import InboundChannelMessage


logger = logging.getLogger(__name__)

LINK_PATTERN = re.compile(
    r"^(?:绑定|bind)\s*[:：]?\s*([2-9A-HJ-NP-Z]{4}-?[2-9A-HJ-NP-Z]{4})$",
    re.IGNORECASE,
)
PRIVATE_CHAT_ONLY_REPLY = "为了保护账户与家庭信息，请私聊机器人办理银行业务。"
UNSUPPORTED_MESSAGE_REPLY = "目前仅支持文本消息，请直接输入银行业务需求。"
SECURE_APP_NOTICE = (
    "为保护账户安全，请回到安心办 Web/App 完成确认或强验证。"
    "飞书内不会接受验证码或资金授权。"
)


class FeishuEventError(ValueError):
    pass


class FeishuReplier(Protocol):
    def reply_text(
        self,
        *,
        message_id: str,
        text: str,
        idempotency_key: str,
    ) -> None: ...

    def reply_card(
        self,
        *,
        message_id: str,
        card: dict[str, Any],
        idempotency_key: str,
    ) -> None: ...


class ChannelGateway(Protocol):
    def process_inbound(self, payload: InboundChannelMessage) -> dict[str, Any]: ...


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


class HttpChannelGatewayClient:
    """通过受保护的内部接口调用唯一的后端业务执行进程。"""

    def __init__(
        self,
        *,
        base_url: str,
        gateway_secret: str,
        timeout_seconds: int = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Channel-Gateway-Key": gateway_secret},
            timeout=timeout_seconds,
            transport=transport,
        )

    def process_inbound(self, payload: InboundChannelMessage) -> dict[str, Any]:
        try:
            response = self.client.post(
                "/api/channels/messages/inbound",
                json=payload.model_dump(mode="json"),
            )
        except httpx.HTTPError as error:
            raise ApplicationError("CHANNEL_PROCESSING_FAILED") from error
        if not response.is_success:
            code = "CHANNEL_PROCESSING_FAILED"
            try:
                detail = response.json().get("detail", {})
                if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                    code = detail["code"]
            except (ValueError, AttributeError):
                pass
            raise ApplicationError(
                code,
                details={"upstream_status": response.status_code},
            )
        try:
            result = response.json()
        except ValueError as error:
            raise ApplicationError("CHANNEL_PROCESSING_FAILED") from error
        if not isinstance(result, dict) or "gateway_message_id" not in result:
            raise ApplicationError("CHANNEL_PROCESSING_FAILED")
        return result

    def close(self) -> None:
        self.client.close()


@dataclass(frozen=True)
class ParsedFeishuEvent:
    message_id: str
    inbound: InboundChannelMessage | None = None
    immediate_reply: str | None = None
    ignored: bool = False


def _event_time(value: Any) -> datetime:
    if value is None or value == "":
        return datetime.now(timezone.utc)
    try:
        timestamp = int(value)
    except (TypeError, ValueError) as error:
        raise FeishuEventError("FEISHU_EVENT_TIME_INVALID") from error
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise FeishuEventError("FEISHU_EVENT_TIME_INVALID") from error


def _sender_external_id(sender: Any) -> str:
    sender_id = _field(sender, "sender_id")
    for field in ("open_id", "user_id", "union_id"):
        value = _field(sender_id, field)
        if value:
            return str(value)
    raise FeishuEventError("FEISHU_SENDER_ID_MISSING")


def parse_feishu_event(event: Any) -> ParsedFeishuEvent:
    event_data = _field(event, "event")
    sender = _field(event_data, "sender")
    message = _field(event_data, "message")
    if sender is None or message is None:
        raise FeishuEventError("FEISHU_EVENT_PAYLOAD_INVALID")

    message_id = str(_field(message, "message_id", "") or "")
    if not message_id:
        raise FeishuEventError("FEISHU_MESSAGE_ID_MISSING")
    if str(_field(sender, "sender_type", "")).lower() != "user":
        return ParsedFeishuEvent(message_id=message_id, ignored=True)

    chat_type = str(_field(message, "chat_type", "") or "")
    if chat_type != "p2p":
        return ParsedFeishuEvent(
            message_id=message_id,
            immediate_reply=PRIVATE_CHAT_ONLY_REPLY,
        )
    if str(_field(message, "message_type", "") or "") != "text":
        return ParsedFeishuEvent(
            message_id=message_id,
            immediate_reply=UNSUPPORTED_MESSAGE_REPLY,
        )

    try:
        content = json.loads(str(_field(message, "content", "") or ""))
    except json.JSONDecodeError as error:
        raise FeishuEventError("FEISHU_TEXT_CONTENT_INVALID") from error
    text = content.get("text") if isinstance(content, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise FeishuEventError("FEISHU_TEXT_CONTENT_INVALID")
    text = text.strip()

    tenant_key = (
        _field(sender, "tenant_key")
        or _field(_field(event, "header"), "tenant_key")
    )
    chat_id = _field(message, "chat_id")
    if not tenant_key or not chat_id:
        raise FeishuEventError("FEISHU_EVENT_SCOPE_MISSING")

    match = LINK_PATTERN.fullmatch(text)
    if match:
        inbound = InboundChannelMessage(
            channel="FEISHU",
            tenant_id=str(tenant_key),
            external_message_id=message_id,
            external_conversation_id=str(chat_id),
            external_user_id=_sender_external_id(sender),
            kind="LINK",
            link_code=match.group(1).upper(),
            occurred_at=_event_time(_field(message, "create_time")),
        )
    else:
        inbound = InboundChannelMessage(
            channel="FEISHU",
            tenant_id=str(tenant_key),
            external_message_id=message_id,
            external_conversation_id=str(chat_id),
            external_user_id=_sender_external_id(sender),
            kind="TEXT",
            text=text,
            occurred_at=_event_time(_field(message, "create_time")),
        )
    return ParsedFeishuEvent(message_id=message_id, inbound=inbound)


def _reply_idempotency_key(message_id: str, gateway_message_id: str | None = None) -> str:
    source = gateway_message_id or message_id
    return "sbg_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def format_gateway_reply(response: dict[str, Any]) -> str:
    reply = response.get("reply") or {}
    text = str(reply.get("text") or "消息已处理。")
    if response.get("security", {}).get("mode") == "APP_REQUIRED":
        text = f"{text}\n\n{SECURE_APP_NOTICE}"
    # 飞书文本上限远大于此值；主动收敛避免异常工具输出进入 IM。
    if len(text) > 4000:
        text = text[:3990] + "\n（内容已截断）"
    return text


def build_gateway_card(response: dict[str, Any]) -> dict[str, Any] | None:
    security = response.get("security") or {}
    deep_link = security.get("deep_link")
    if security.get("mode") != "APP_REQUIRED" or not isinstance(deep_link, str):
        return None

    reply = response.get("reply") or {}
    text = str(reply.get("text") or "操作等待处理。")
    risk_level = str(security.get("risk_level") or "YELLOW").upper()
    is_verification = security.get("action") == "OPEN_APP_VERIFICATION"
    title = "银行操作需要强验证" if is_verification else "银行操作等待确认"
    template = "red" if is_verification or risk_level == "RED" else "orange"
    action_label = str(security.get("action_label") or "前往安心办处理")
    return {
        "config": {"wide_screen_mode": True, "enable_forward": False},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": text}},
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "plain_text",
                    "content": SECURE_APP_NOTICE,
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": action_label},
                        "type": "primary",
                        "url": deep_link,
                    }
                ],
            },
        ],
    }


def deliver_parsed_event(
    gateway: ChannelGateway,
    replier: FeishuReplier,
    parsed: ParsedFeishuEvent,
) -> dict[str, Any] | None:
    """长连接和 Webhook 共用的唯一回复与幂等契约。"""
    if parsed.ignored:
        return None
    if parsed.immediate_reply is not None:
        replier.reply_text(
            message_id=parsed.message_id,
            text=parsed.immediate_reply,
            idempotency_key=_reply_idempotency_key(parsed.message_id),
        )
        return None
    if parsed.inbound is None:
        return None
    response = gateway.process_inbound(parsed.inbound)
    idempotency_key = _reply_idempotency_key(
        parsed.message_id,
        response.get("gateway_message_id"),
    )
    card = build_gateway_card(response)
    if card is not None:
        replier.reply_card(
            message_id=parsed.message_id,
            card=card,
            idempotency_key=idempotency_key,
        )
    else:
        replier.reply_text(
            message_id=parsed.message_id,
            text=format_gateway_reply(response),
            idempotency_key=idempotency_key,
        )
    return response


class FeishuMessageProcessor:
    def __init__(
        self,
        gateway: ChannelGateway,
        replier: FeishuReplier,
        *,
        executor: Executor | None = None,
        worker_threads: int = 1,
    ) -> None:
        self.gateway = gateway
        self.replier = replier
        self._owns_executor = executor is None
        self.executor = executor or ThreadPoolExecutor(
            max_workers=worker_threads,
            thread_name_prefix="feishu-agent",
        )

    def handle_event(self, event: Any) -> None:
        """只解析并入队，确保飞书长连接事件能够快速确认。"""
        try:
            parsed = parse_feishu_event(event)
        except FeishuEventError as error:
            logger.warning("飞书事件被拒绝：%s", error)
            return
        if parsed.ignored:
            return
        try:
            self.executor.submit(self.process, parsed)
        except RuntimeError:
            logger.error("飞书处理队列已关闭，消息未进入 Agent。")

    def process(self, parsed: ParsedFeishuEvent) -> None:
        try:
            deliver_parsed_event(self.gateway, self.replier, parsed)
        except ApplicationError as error:
            logger.warning("飞书消息处理失败：%s", error.code)
            self._reply_failure(parsed.message_id, error.message)
        except Exception:
            logger.exception("飞书消息处理发生未预期错误。")
            self._reply_failure(parsed.message_id, "服务暂时不可用，请稍后重试。")

    def _reply_failure(self, message_id: str, message: str) -> None:
        try:
            self.replier.reply_text(
                message_id=message_id,
                text=message,
                idempotency_key=_reply_idempotency_key(message_id),
            )
        except Exception:
            logger.exception("飞书失败提示发送失败。")

    def close(self) -> None:
        if self._owns_executor and isinstance(self.executor, ThreadPoolExecutor):
            self.executor.shutdown(wait=True, cancel_futures=False)
