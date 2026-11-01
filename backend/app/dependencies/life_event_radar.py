"""生活事件雷达依赖。"""

from functools import lru_cache

import lark_oapi as lark

from backend.app.core.config import settings
from backend.app.errors import ApplicationError
from backend.app.integrations.feishu_long_connection import FeishuProactiveSender
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.repositories.life_event_radar import LifeEventRadarRepository
from backend.app.repositories.memory import repository
from backend.app.security.channel_identity_cipher import ChannelIdentityCipher
from backend.app.services.life_event_radar import LifeEventRadarService


@lru_cache(maxsize=1)
def get_life_event_radar_service() -> LifeEventRadarService:
    engine = getattr(repository, "engine", None)
    if engine is None:
        raise ApplicationError("LIFE_RADAR_NOT_CONFIGURED")

    cipher = None
    if settings.channel_identity_pepper is not None:
        cipher = ChannelIdentityCipher(
            settings.channel_identity_pepper.get_secret_value()
        )

    sender = None
    if settings.feishu_configured and settings.feishu_app_secret is not None:
        client = (
            lark.Client.builder()
            .app_id(settings.feishu_app_id)
            .app_secret(settings.feishu_app_secret.get_secret_value())
            .build()
        )
        sender = FeishuProactiveSender(client)

    simulation_enabled = settings.life_radar_simulation_enabled and settings.environment.lower() in {
        "local",
        "development",
        "test",
        "demo",
    }
    return LifeEventRadarService(
        LifeEventRadarRepository(
            engine, retry_base_seconds=settings.life_radar_retry_base_seconds
        ),
        ChannelGatewayRepository(engine),
        identity_cipher=cipher,
        sender=sender,
        trusted_app_base_url=settings.trusted_app_base_url,
        batch_size=settings.life_radar_batch_size,
        lease_seconds=settings.life_radar_lease_seconds,
        simulation_enabled=simulation_enabled,
    )
