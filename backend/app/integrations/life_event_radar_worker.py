"""生活事件雷达独立 Worker：python -m ... [--once]。"""

from __future__ import annotations

import argparse
import logging
import socket
import time
from uuid import uuid4

import lark_oapi as lark

from backend.app.core.config import settings
from backend.app.db.database import create_database_engine
from backend.app.integrations.feishu_long_connection import FeishuProactiveSender
from backend.app.integrations.channel_worker_heartbeat import (
    ChannelWorkerHeartbeatClient,
)
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.repositories.life_event_radar import LifeEventRadarRepository
from backend.app.security.channel_identity_cipher import ChannelIdentityCipher
from backend.app.services.life_event_radar import LifeEventRadarService


logger = logging.getLogger(__name__)


def build_service() -> LifeEventRadarService:
    if not settings.life_radar_configured or settings.feishu_app_secret is None:
        raise RuntimeError(
            "LIFE_RADAR_NOT_CONFIGURED：请配置 MySQL、CHANNEL_GATEWAY_SECRET、"
            "CHANNEL_IDENTITY_PEPPER、FEISHU_APP_ID 和 FEISHU_APP_SECRET"
        )
    engine = create_database_engine()
    client = (
        lark.Client.builder()
        .app_id(settings.feishu_app_id)
        .app_secret(settings.feishu_app_secret.get_secret_value())
        .build()
    )
    return LifeEventRadarService(
        LifeEventRadarRepository(
            engine, retry_base_seconds=settings.life_radar_retry_base_seconds
        ),
        ChannelGatewayRepository(engine),
        identity_cipher=ChannelIdentityCipher(
            settings.channel_identity_pepper.get_secret_value()
        ),
        sender=FeishuProactiveSender(client),
        trusted_app_base_url=settings.trusted_app_base_url,
        batch_size=settings.life_radar_batch_size,
        lease_seconds=settings.life_radar_lease_seconds,
        simulation_enabled=settings.life_radar_simulation_enabled,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="安心办生活事件雷达 Worker")
    parser.add_argument("--once", action="store_true", help="只扫描一次后退出")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, settings.feishu_log_level))
    service = build_service()
    worker_id = f"radar:{socket.gethostname()}:{uuid4().hex[:12]}"
    heartbeat = ChannelWorkerHeartbeatClient(
        base_url=settings.channel_gateway_base_url,
        gateway_secret=settings.channel_gateway_secret.get_secret_value(),
        worker_id=worker_id,
        worker_kind="LIFE_RADAR",
        transport="SCHEDULER",
        interval_seconds=settings.channel_worker_heartbeat_seconds,
        lease_seconds=settings.channel_worker_lease_seconds,
    )
    heartbeat.start()
    try:
        while True:
            try:
                result = service.scan_once(worker_id=worker_id)
                heartbeat.set_state("ONLINE")
                logger.info(
                    "雷达扫描完成 claimed=%s sent=%s failed=%s canceled=%s",
                    result["claimed"],
                    result["sent"],
                    result["failed"],
                    result["canceled"],
                )
            except Exception:
                heartbeat.set_state("DEGRADED", "LIFE_RADAR_SCAN_FAILED")
                logger.exception("雷达扫描失败，本轮未声明通知成功。")
            if args.once:
                return
            time.sleep(settings.life_radar_poll_seconds)
    finally:
        heartbeat.close()


if __name__ == "__main__":
    main()
