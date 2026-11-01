"""当前用户的家庭资金协商与有限授权范围接口。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status

from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.family_funding import get_family_funding_service
from backend.app.schemas.family_funding import (
    CompleteFundingConstraintsRequest,
    CreateFundingConsentScopeRequest,
    CreateFundingNegotiationRequest,
    FundingDecisionRequest,
    ProposeCustomFundingPlanRequest,
    SelectFundingPlanRequest,
)
from backend.app.services.auth_service import AuthContext
from backend.app.services.family_funding_negotiation import FamilyFundingNegotiationService


router = APIRouter()


@router.get("/context")
def get_funding_context(
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.context(context.user_id)


@router.post("/negotiations")
def create_negotiation(
    payload: CreateFundingNegotiationRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    result = service.create(
        owner_user_id=context.user_id,
        idempotency_key=idempotency_key,
        request_text=payload.request_text,
        planning=payload.planning,
        source_run_id=payload.source_run_id,
    )
    response.status_code = status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK
    return result


@router.get("/negotiations")
def list_negotiations(
    negotiation_status: Annotated[
        Literal[
            "WAITING_INPUT", "WAITING_SELECTION", "PENDING_CONFIRMATION", "APPLIED",
            "REJECTED", "CANCELED", "STALE", "WAITING_HUMAN", "UNDONE",
        ] | None,
        Query(alias="status"),
    ] = None,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    items = service.list(context.user_id, negotiation_status)
    return {"items": items, "total": len(items)}


@router.get("/negotiations/{negotiation_id}")
def get_negotiation(
    negotiation_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.get(context.user_id, negotiation_id)


@router.get("/negotiations/{negotiation_id}/audits")
def list_negotiation_audits(
    negotiation_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    items = service.audits(context.user_id, negotiation_id)
    return {"items": items, "total": len(items)}


@router.put("/negotiations/{negotiation_id}/constraints")
def complete_constraints(
    negotiation_id: str,
    payload: CompleteFundingConstraintsRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.complete_constraints(
        owner_user_id=context.user_id,
        negotiation_id=negotiation_id,
        planning=payload.planning,
        reason=payload.reason,
    )


@router.post("/negotiations/{negotiation_id}/model-plans")
def add_model_plan(
    negotiation_id: str,
    payload: ProposeCustomFundingPlanRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.add_custom_plan(
        owner_user_id=context.user_id,
        negotiation_id=negotiation_id,
        title=payload.title,
        model_explanation=payload.model_explanation,
        adjustments=payload.adjustments,
    )


@router.post("/negotiations/{negotiation_id}/select")
def select_plan(
    negotiation_id: str,
    payload: SelectFundingPlanRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.select_plan(
        owner_user_id=context.user_id,
        negotiation_id=negotiation_id,
        plan_id=payload.plan_id,
        reason=payload.reason,
    )


@router.post("/negotiations/{negotiation_id}/confirm")
def confirm_plan(
    negotiation_id: str,
    payload: FundingDecisionRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.confirm(
        owner_user_id=context.user_id,
        negotiation_id=negotiation_id,
        reason=payload.reason,
    )


@router.post("/negotiations/{negotiation_id}/{command}")
def transition_negotiation(
    negotiation_id: str,
    command: Literal["reject", "cancel", "handoff", "undo"],
    payload: FundingDecisionRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    if command == "undo":
        return service.undo(
            owner_user_id=context.user_id,
            negotiation_id=negotiation_id,
            reason=payload.reason,
        )
    return service.transition(
        owner_user_id=context.user_id,
        negotiation_id=negotiation_id,
        command=command,
        reason=payload.reason,
    )


@router.post("/consent-scopes", status_code=status.HTTP_201_CREATED)
def create_consent_scope(
    payload: CreateFundingConsentScopeRequest,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.create_scope(owner_user_id=context.user_id, payload=payload)


@router.get("/consent-scopes")
def list_consent_scopes(
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    items = service.list_scopes(context.user_id)
    return {"items": items, "total": len(items)}


@router.delete("/consent-scopes/{scope_id}")
def revoke_consent_scope(
    scope_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: FamilyFundingNegotiationService = Depends(get_family_funding_service),
) -> dict:
    return service.revoke_scope(owner_user_id=context.user_id, scope_id=scope_id)
