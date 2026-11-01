from fastapi import APIRouter

from backend.app.core.config import settings
from backend.app.repositories.memory import repository


router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    persistence = repository.persistence_status()
    return {
        "status": "ok" if persistence["connected"] and "fallback_reason" not in persistence else "degraded",
        "persistence": persistence,
        "integrations": {
            "feishu": {
                "mode": "LONG_CONNECTION",
                "configured": settings.feishu_configured,
                "worker_process_required": True,
            }
        },
    }

