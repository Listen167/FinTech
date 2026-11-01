"""生活关系图服务依赖。"""

from functools import lru_cache

from fastapi import HTTPException, status

from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.repositories.memory import repository
from backend.app.services.life_graph_service import LifeGraphService


@lru_cache
def get_life_graph_service() -> LifeGraphService:
    engine = getattr(repository, "engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "LIFE_GRAPH_NOT_CONFIGURED",
                "message": "生活关系图服务需要启用数据库。",
            },
        )
    graph_repository = LifeGraphRepository(engine)
    return LifeGraphService(engine, graph_repository)
