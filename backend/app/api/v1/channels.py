"""用户侧渠道绑定管理与适配器统一入站接口。"""

from fastapi import APIRouter, Depends, Response, status

from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.channel_gateway import (
    get_channel_gateway_service,
    require_channel_gateway_key,
)
from backend.app.schemas.channel import (
    CreateChannelLinkTokenRequest,
    InboundChannelMessage,
)
from backend.app.services.auth_service import AuthContext
from backend.app.services.channel_gateway import ChannelGatewayService


router = APIRouter()


@router.post("/link-tokens", status_code=status.HTTP_201_CREATED)
def create_link_token(
    payload: CreateChannelLinkTokenRequest,
    context: AuthContext = Depends(get_current_auth),
    service: ChannelGatewayService = Depends(get_channel_gateway_service),
) -> dict:
    return service.create_link_token(user_id=context.user_id, channel=payload.channel)


@router.get("/identities")
def list_identities(
    context: AuthContext = Depends(get_current_auth),
    service: ChannelGatewayService = Depends(get_channel_gateway_service),
) -> dict:
    items = service.list_identities(user_id=context.user_id)
    return {"items": items, "total": len(items)}


@router.delete("/identities/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_identity(
    identity_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: ChannelGatewayService = Depends(get_channel_gateway_service),
) -> Response:
    service.revoke_identity(user_id=context.user_id, identity_id=identity_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages/inbound")
def process_inbound_message(
    payload: InboundChannelMessage,
    _: None = Depends(require_channel_gateway_key),
    service: ChannelGatewayService = Depends(get_channel_gateway_service),
) -> dict:
    return service.process_inbound(payload)
