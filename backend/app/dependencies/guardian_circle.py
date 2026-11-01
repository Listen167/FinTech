"""家庭守护圈服务依赖。"""

from functools import lru_cache

from fastapi import HTTPException, status

from backend.app.core.config import settings
from backend.app.repositories.memory import repository
from backend.app.services.guardian_circle import GuardianCircleService


@lru_cache
def get_guardian_circle_service() -> GuardianCircleService:
    engine = getattr(repository, "engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GUARDIAN_CIRCLE_NOT_CONFIGURED",
                "message": "家庭守护圈需要启用数据库。",
            },
        )
    return GuardianCircleService(
        engine,
        invite_ttl_seconds=settings.guardian_invite_ttl_seconds,
    )
