"""当前用户的生活关系图与候选变更接口。"""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.life_graph import get_life_graph_service
from backend.app.dependencies.life_goal_conflicts import get_life_goal_conflict_service
from backend.app.schemas.life_graph import (
    CreateLifeGraphChangeRequest,
    LifeGraphDecisionRequest,
    LifeGraphNodeCorrection,
)
from backend.app.services.auth_service import AuthContext
from backend.app.services.agent_runtime import runtime
from backend.app.services.life_goal_conflict_service import (
    LifeGoalConflictError,
    LifeGoalConflictService,
)
from backend.app.services.life_graph_service import LifeGraphService, LifeGraphServiceError


router = APIRouter()


def _translate_error(error: LifeGraphServiceError | LifeGoalConflictError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.public_detail(),
    )


@router.get("")
def get_graph(
    context: AuthContext = Depends(get_current_auth),
    service: LifeGraphService = Depends(get_life_graph_service),
) -> dict:
    return service.get_graph(context.user_id)


@router.get("/goal-conflicts")
def analyze_goal_conflicts(
    protected_balance: Annotated[Decimal, Query(ge=0, max_digits=14, decimal_places=2)] = Decimal("0.00"),
    horizon_months: Annotated[int, Query(ge=1, le=24)] = 12,
    context: AuthContext = Depends(get_current_auth),
    service: LifeGoalConflictService = Depends(get_life_goal_conflict_service),
) -> dict:
    try:
        return service.analyze(
            owner_user_id=context.user_id,
            protected_balance=protected_balance,
            horizon_months=horizon_months,
        )
    except LifeGoalConflictError as error:
        raise _translate_error(error) from error


@router.patch("/nodes/{node_id}")
def correct_life_graph_node(
    node_id: str,
    payload: LifeGraphNodeCorrection,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
    context: AuthContext = Depends(get_current_auth),
    service: LifeGraphService = Depends(get_life_graph_service),
) -> dict:
    try:
        return service.correct_node(
            owner_user_id=context.user_id,
            node_id=node_id,
            correction=payload,
            idempotency_key=idempotency_key,
        )
    except LifeGraphServiceError as error:
        raise _translate_error(error) from error


@router.get("/change-requests")
def list_change_requests(
    request_status: Annotated[
        Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED", "CANCELED"] | None,
        Query(alias="status"),
    ] = None,
    context: AuthContext = Depends(get_current_auth),
    service: LifeGraphService = Depends(get_life_graph_service),
) -> dict:
    items = service.list_change_requests(context.user_id, status=request_status)
    return {"items": items, "total": len(items)}


@router.post("/change-requests")
def create_change_request(
    payload: CreateLifeGraphChangeRequest,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
    context: AuthContext = Depends(get_current_auth),
    service: LifeGraphService = Depends(get_life_graph_service),
) -> dict:
    try:
        result = service.create_change_request(
            owner_user_id=context.user_id,
            idempotency_key=idempotency_key,
            summary=payload.summary,
            change_set=payload.change_set,
            source_run_id=payload.source_run_id,
        )
    except LifeGraphServiceError as error:
        raise _translate_error(error) from error
    response.status_code = (
        status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK
    )
    return result


@router.get("/change-requests/{request_id}")
def get_change_request(
    request_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: LifeGraphService = Depends(get_life_graph_service),
) -> dict:
    try:
        return service.get_change_request(context.user_id, request_id)
    except LifeGraphServiceError as error:
        raise _translate_error(error) from error


@router.get("/change-requests/{request_id}/goal-conflicts")
def analyze_candidate_goal_conflicts(
    request_id: str,
    protected_balance: Annotated[Decimal, Query(ge=0, max_digits=14, decimal_places=2)] = Decimal("0.00"),
    horizon_months: Annotated[int, Query(ge=1, le=24)] = 12,
    context: AuthContext = Depends(get_current_auth),
    service: LifeGoalConflictService = Depends(get_life_goal_conflict_service),
) -> dict:
    try:
        return service.analyze(
            owner_user_id=context.user_id,
            candidate_request_id=request_id,
            protected_balance=protected_balance,
            horizon_months=horizon_months,
        )
    except LifeGoalConflictError as error:
        raise _translate_error(error) from error


@router.post("/change-requests/{request_id}/decide")
def decide_change_request(
    request_id: str,
    payload: LifeGraphDecisionRequest,
    context: AuthContext = Depends(get_current_auth),
    service: LifeGraphService = Depends(get_life_graph_service),
) -> dict:
    try:
        return runtime.decide_life_graph_change(
            request_id=request_id,
            approved=payload.approved,
            reason=payload.reason,
            user_id=context.user_id,
            service=service,
        )
    except LifeGraphServiceError as error:
        raise _translate_error(error) from error
