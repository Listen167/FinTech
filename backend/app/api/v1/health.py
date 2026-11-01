from fastapi import APIRouter

from backend.app.core.config import settings
from backend.app.repositories.memory import repository
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.services.channel_gateway import summarize_channel_status


router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    persistence = repository.persistence_status()
    workers = []
    worker_status_available = False
    engine = getattr(repository, "engine", None)
    if engine is not None and settings.channel_gateway_configured:
        try:
            workers = ChannelGatewayRepository(engine).list_worker_statuses()
            worker_status_available = True
        except Exception:
            workers = []
    channel_status = summarize_channel_status(
        workers,
        gateway_configured=settings.channel_gateway_configured,
        feishu_configured=settings.feishu_configured,
        webhook_configured=settings.feishu_webhook_configured,
    )
    return {
        "status": "ok" if persistence["connected"] and "fallback_reason" not in persistence else "degraded",
        "persistence": persistence,
        "integrations": {
            "feishu": {
                "configured": settings.feishu_configured,
                "gateway_configured": settings.channel_gateway_configured,
                "webhook_configured": settings.feishu_webhook_configured,
                "online": channel_status["online"],
                "worker_status_available": worker_status_available,
                "transports": channel_status["transports"],
            }
        },
    }

