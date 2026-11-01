"""评测与运行指标服务依赖。"""

from functools import lru_cache

from backend.app.repositories.memory import repository
from backend.app.services.observability import ObservabilityService


@lru_cache
def get_observability_service() -> ObservabilityService:
    return ObservabilityService(repository)
