"""飞书官方 SDK 长连接 Worker；必须与 Uvicorn 分进程运行。"""

from __future__ import annotations

import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from backend.app.core.config import settings
from backend.app.integrations.feishu_adapter import (
    FeishuMessageProcessor,
    HttpChannelGatewayClient,
)


logger = logging.getLogger(__name__)


class FeishuReplyClient:
    def __init__(self, client: lark.Client) -> None:
        self.client = client

    def reply_text(
        self,
        *,
        message_id: str,
        text: str,
        idempotency_key: str,
    ) -> None:
        self._reply(
            message_id=message_id,
            msg_type="text",
            content={"text": text},
            idempotency_key=idempotency_key,
        )

    def reply_card(
        self,
        *,
        message_id: str,
        card: dict,
        idempotency_key: str,
    ) -> None:
        self._reply(
            message_id=message_id,
            msg_type="interactive",
            content=card,
            idempotency_key=idempotency_key,
        )

    def _reply(
        self,
        *,
        message_id: str,
        msg_type: str,
        content: dict,
        idempotency_key: str,
    ) -> None:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type(msg_type)
            .content(json.dumps(content, ensure_ascii=False))
            .uuid(idempotency_key)
            .build()
        )
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = self.client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(
                f"FEISHU_REPLY_FAILED code={response.code} "
                f"log_id={response.get_log_id()}"
            )


class FeishuProactiveSender:
    """只向绑定用户的 open_id 私聊发送最小披露提醒。"""

    def __init__(self, client: lark.Client) -> None:
        self.client = client

    def send_text(
        self,
        *,
        receive_id: str,
        text: str,
        idempotency_key: str,
    ) -> dict[str, str | None]:
        from uuid import NAMESPACE_URL, uuid5

        stable_uuid = str(uuid5(NAMESPACE_URL, f"safebank-radar:{idempotency_key}"))
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .uuid(stable_uuid)
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(body)
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(
                f"FEISHU_PROACTIVE_SEND_FAILED code={response.code} "
                f"log_id={response.get_log_id()}"
            )
        return {
            "message_id": getattr(response.data, "message_id", None),
            "log_id": response.get_log_id(),
        }


def _log_level() -> lark.LogLevel:
    return {
        "DEBUG": lark.LogLevel.DEBUG,
        "INFO": lark.LogLevel.INFO,
        "WARNING": lark.LogLevel.WARNING,
        "ERROR": lark.LogLevel.ERROR,
    }[settings.feishu_log_level]


def main() -> None:
    if not settings.feishu_configured or settings.feishu_app_secret is None:
        raise RuntimeError(
            "FEISHU_NOT_CONFIGURED：请在 .env 填写 FEISHU_APP_ID 和 FEISHU_APP_SECRET"
        )
    if settings.channel_gateway_secret is None:
        raise RuntimeError(
            "CHANNEL_GATEWAY_NOT_CONFIGURED：请在 .env 填写 CHANNEL_GATEWAY_SECRET"
        )

    level = _log_level()
    api_client = (
        lark.Client.builder()
        .app_id(settings.feishu_app_id)
        .app_secret(settings.feishu_app_secret.get_secret_value())
        .log_level(level)
        .build()
    )
    gateway_client = HttpChannelGatewayClient(
        base_url=settings.channel_gateway_base_url,
        gateway_secret=settings.channel_gateway_secret.get_secret_value(),
        timeout_seconds=settings.channel_gateway_timeout_seconds,
    )
    processor = FeishuMessageProcessor(
        gateway_client,
        FeishuReplyClient(api_client),
        worker_threads=settings.feishu_worker_threads,
    )
    event_handler = (
        lark.EventDispatcherHandler.builder("", "", level)
        .register_p2_im_message_receive_v1(processor.handle_event)
        .build()
    )
    ws_client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret.get_secret_value(),
        log_level=level,
        event_handler=event_handler,
        auto_reconnect=True,
    )

    logger.info("飞书长连接 Worker 正在启动。")
    try:
        ws_client.start()
    finally:
        processor.close()
        gateway_client.close()


if __name__ == "__main__":
    main()
