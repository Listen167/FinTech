"""当前用户的主动生活事件提醒查询、控制与沙箱时间接口。"""

from fastapi import APIRouter, Depends

from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.life_event_radar import get_life_event_radar_service
from backend.app.schemas.life_event_radar import RadarSimulationRequest
from backend.app.services.auth_service import AuthContext
from backend.app.services.life_event_radar import LifeEventRadarService


router = APIRouter()


@router.get("/status")
def get_radar_status(
    _: AuthContext = Depends(get_current_auth),
    service: LifeEventRadarService = Depends(get_life_event_radar_service),
) -> dict:
    return service.status()


@router.get("/actions")
def list_radar_actions(
    context: AuthContext = Depends(get_current_auth),
    service: LifeEventRadarService = Depends(get_life_event_radar_service),
) -> dict:
    items = service.list_actions(context.user_id)
    return {"items": items, "total": len(items)}


@router.get("/actions/{action_id}/audits")
def list_action_audits(
    action_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: LifeEventRadarService = Depends(get_life_event_radar_service),
) -> dict:
    items = service.list_audits(context.user_id, action_id)
    return {"items": items, "total": len(items)}


@router.post("/actions/{action_id}/pause")
def pause_action(
    action_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: LifeEventRadarService = Depends(get_life_event_radar_service),
) -> dict:
    return service.control_action(
        owner_user_id=context.user_id, action_id=action_id, command="PAUSE"
    )


@router.post("/actions/{action_id}/resume")
def resume_action(
    action_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: LifeEventRadarService = Depends(get_life_event_radar_service),
) -> dict:
    return service.control_action(
        owner_user_id=context.user_id, action_id=action_id, command="RESUME"
    )


@router.post("/actions/{action_id}/cancel")
def cancel_action(
    action_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: LifeEventRadarService = Depends(get_life_event_radar_service),
) -> dict:
    return service.control_action(
        owner_user_id=context.user_id, action_id=action_id, command="CANCEL"
    )


@router.post("/demo/scan")
def simulate_radar_scan(
    payload: RadarSimulationRequest,
    context: AuthContext = Depends(get_current_auth),
    service: LifeEventRadarService = Depends(get_life_event_radar_service),
) -> dict:
    return service.simulate_scan(
        owner_user_id=context.user_id, advance_days=payload.advance_days
    )
