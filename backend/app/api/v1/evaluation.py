"""比赛评测报告与聚合运行指标接口。"""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.observability import get_observability_service
from backend.app.services.auth_service import AuthContext
from backend.app.services.observability import ObservabilityError, ObservabilityService


router = APIRouter()


@router.get("/summary")
def evaluation_summary(
    _: AuthContext = Depends(get_current_auth),
    service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    try:
        return service.evaluation_summary()
    except ObservabilityError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": str(error),
                "message": "评测基线报告不可用，请先运行离线评测。",
                "retryable": True,
            },
        ) from error


@router.get("/runtime-metrics")
def runtime_metrics(
    _: AuthContext = Depends(get_current_auth),
    service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return service.runtime_metrics()
