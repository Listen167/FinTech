"""生活资金目标冲突服务依赖。"""

from functools import lru_cache

from fastapi import HTTPException, status

from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.repositories.memory import repository
from backend.app.services.life_goal_conflict_service import LifeGoalConflictService


@lru_cache
def get_life_goal_conflict_service() -> LifeGoalConflictService:
    engine = getattr(repository, "engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "LIFE_GOAL_CONFLICT_SERVICE_NOT_CONFIGURED",
                "message": "资金目标冲突检测需要启用数据库。",
            },
        )
    return LifeGoalConflictService(engine, LifeGraphRepository(engine))
