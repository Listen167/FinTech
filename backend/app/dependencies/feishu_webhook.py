"""飞书 Webhook 处理器依赖。"""

from functools import lru_cache

import lark_oapi as lark

from backend.app.core.config import settings
from backend.app.dependencies.channel_gateway import get_channel_gateway_service
from backend.app.errors import ApplicationError
from backend.app.integrations.feishu_long_connection import FeishuReplyClient
from backend.app.integrations.feishu_webhook import (
    FeishuWebhookHandler,
    FeishuWebhookVerifier,
)


@lru_cache(maxsize=1)
def get_feishu_webhook_handler() -> FeishuWebhookHandler:
    if (
        not settings.feishu_webhook_configured
        or settings.feishu_app_secret is None
        or settings.feishu_verification_token is None
        or settings.feishu_encrypt_key is None
    ):
        raise ApplicationError("FEISHU_WEBHOOK_NOT_CONFIGURED")
    api_client = (
        lark.Client.builder()
        .app_id(settings.feishu_app_id)
        .app_secret(settings.feishu_app_secret.get_secret_value())
        .build()
    )
    return FeishuWebhookHandler(
        verifier=FeishuWebhookVerifier(
            verification_token=settings.feishu_verification_token.get_secret_value(),
            encrypt_key=settings.feishu_encrypt_key.get_secret_value(),
            max_skew_seconds=settings.feishu_webhook_max_skew_seconds,
        ),
        gateway=get_channel_gateway_service(),
        replier=FeishuReplyClient(api_client),
        worker_lease_seconds=settings.channel_worker_lease_seconds,
    )
