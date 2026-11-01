"""当前用户的反诈冷静期查询、申诉与受信 App 强验证接口。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.fraud_cooling import get_fraud_cooling_service
from backend.app.schemas.fraud_cooling import (
    FraudAppealRequest,
    FraudReleaseVerificationRequest,
)
from backend.app.services.auth_service import AuthContext
from backend.app.services.fraud_cooling import FraudCoolingService


router = APIRouter()


@router.get("/events")
def list_fraud_events(
    event_status: Annotated[
        Literal["COOLING_OFF", "RELEASED", "EXPIRED"] | None,
        Query(alias="status"),
    ] = None,
    context: AuthContext = Depends(get_current_auth),
    service: FraudCoolingService = Depends(get_fraud_cooling_service),
) -> dict:
    items = service.list(context.user_id, event_status)
    return {"items": items, "total": len(items)}


@router.get("/events/{event_id}")
def get_fraud_event(
    event_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: FraudCoolingService = Depends(get_fraud_cooling_service),
) -> dict:
    return service.get(context.user_id, event_id)


@router.get("/events/{event_id}/audits")
def list_fraud_event_audits(
    event_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: FraudCoolingService = Depends(get_fraud_cooling_service),
) -> dict:
    items = service.audits(context.user_id, event_id)
    return {"items": items, "total": len(items)}


@router.post("/events/{event_id}/appeals")
def submit_fraud_appeal(
    event_id: str,
    payload: FraudAppealRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FraudCoolingService = Depends(get_fraud_cooling_service),
) -> dict:
    return service.submit_appeal(
        owner_user_id=context.user_id,
        event_id=event_id,
        reason=payload.reason,
    )


@router.post(
    "/events/{event_id}/release-challenges",
    status_code=status.HTTP_201_CREATED,
)
def create_fraud_release_challenge(
    event_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: FraudCoolingService = Depends(get_fraud_cooling_service),
) -> dict:
    # 此路由只接受银行 App JWT；Channel Gateway 没有调用或代理该能力。
    return service.create_release_challenge(
        owner_user_id=context.user_id,
        event_id=event_id,
        channel="TRUSTED_APP",
    )


@router.post("/release-challenges/{challenge_id}/verify")
def verify_fraud_release_challenge(
    challenge_id: str,
    payload: FraudReleaseVerificationRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FraudCoolingService = Depends(get_fraud_cooling_service),
) -> dict:
    return service.verify_release(
        owner_user_id=context.user_id,
        challenge_id=challenge_id,
        code=payload.code,
        channel="TRUSTED_APP",
    )

