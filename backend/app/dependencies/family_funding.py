"""家庭资金协商服务依赖。"""

from functools import lru_cache

from fastapi import HTTPException, status

from backend.app.repositories.memory import repository
from backend.app.services.family_funding_negotiation import FamilyFundingNegotiationService


@lru_cache
def get_family_funding_service() -> FamilyFundingNegotiationService:
    engine = getattr(repository, "engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "FUNDING_NEGOTIATION_NOT_CONFIGURED",
                "message": "家庭资金协商服务需要启用数据库。",
            },
        )
    return FamilyFundingNegotiationService(engine)
