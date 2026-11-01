"""Channel Gateway 服务与适配器认证依赖。"""

import hmac
from functools import lru_cache
from typing import Annotated

from fastapi import Header

from backend.app.core.config import settings
from backend.app.errors import ApplicationError
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.services.agent_runtime import runtime
from backend.app.services.channel_gateway import ChannelGatewayService


@lru_cache(maxsize=1)
def get_channel_gateway_service() -> ChannelGatewayService:
    engine = getattr(runtime.repo, "engine", None)
    if (
        engine is None
        or not settings.channel_gateway_configured
        or settings.channel_identity_pepper is None
    ):
        raise ApplicationError("CHANNEL_GATEWAY_NOT_CONFIGURED")
    return ChannelGatewayService(
        ChannelGatewayRepository(engine),
        runtime,
        identity_pepper=settings.channel_identity_pepper.get_secret_value(),
        link_token_ttl_seconds=settings.channel_link_token_ttl_seconds,
        trusted_app_base_url=settings.trusted_app_base_url,
    )


def require_channel_gateway_key(
    gateway_key: Annotated[
        str | None,
        Header(alias="X-Channel-Gateway-Key"),
    ] = None,
) -> None:
    configured = settings.channel_gateway_secret
    if configured is None:
        raise ApplicationError("CHANNEL_GATEWAY_NOT_CONFIGURED")
    if gateway_key is None or not hmac.compare_digest(
        gateway_key,
        configured.get_secret_value(),
    ):
        raise ApplicationError("CHANNEL_GATEWAY_UNAUTHORIZED")
