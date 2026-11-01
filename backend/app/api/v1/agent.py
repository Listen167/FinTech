from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from ai.agent.llm_parser import get_model_status
from backend.app.dependencies.auth import get_current_auth
from backend.app.errors import error_definition
from backend.app.schemas.agent import (
    AbortRunRequest,
    ConfirmationDecisionRequest,
    CreateAgentRunRequest,
    HumanHandoffRequest,
    VerificationCodeRequest,
)
from backend.app.services.agent_runtime import runtime
from backend.app.services.auth_service import AuthContext


router = APIRouter()


def _translate_error(error: Exception) -> HTTPException:
    code = str(error.args[0]) if error.args else "INTERNAL_ERROR"
    definition = error_definition(code)
    return HTTPException(
        status_code=definition.status_code,
        detail={
            "code": code,
            "message": definition.message,
            "retryable": definition.retryable,
        },
    )


@router.get("/model-status")
def model_status() -> dict:
    return get_model_status()


@router.post("/sessions", status_code=201)
def create_session(context: AuthContext = Depends(get_current_auth)) -> dict:
    return runtime.create_session(user_id=context.user_id)


@router.get("/sessions")
def list_sessions(context: AuthContext = Depends(get_current_auth)) -> dict:
    items = runtime.list_sessions(user_id=context.user_id)
    return {"items": items, "total": len(items)}


@router.post("/runs")
def create_run(
    request: CreateAgentRunRequest,
    context: AuthContext = Depends(get_current_auth),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    try:
        return runtime.create_run(
            request.session_id,
            request.message,
            idempotency_key,
            user_id=context.user_id,
        )
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.get("/runs/{run_id}")
def get_run(run_id: str, context: AuthContext = Depends(get_current_auth)) -> dict:
    try:
        return runtime.get_run(run_id, user_id=context.user_id)
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.get("/sessions/{session_id}")
def get_session(session_id: str, context: AuthContext = Depends(get_current_auth)) -> dict:
    try:
        return runtime.get_session(session_id, user_id=context.user_id)
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.post("/runs/{run_id}/abort")
def abort_run(
    run_id: str,
    _: AbortRunRequest,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    try:
        return runtime.abort(run_id, user_id=context.user_id)
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.post("/runs/{run_id}/handoff")
def handoff_run(
    run_id: str,
    request: HumanHandoffRequest,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    try:
        return runtime.request_human_handoff(
            run_id,
            user_id=context.user_id,
            reason=request.reason,
        )
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.post("/confirmations/{confirmation_id}/decide")
def decide_confirmation(
    confirmation_id: str,
    request: ConfirmationDecisionRequest,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    try:
        return runtime.decide_confirmation(
            confirmation_id, request.confirmed, user_id=context.user_id
        )
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.post("/verifications/{verification_id}/verify")
def verify_operation(
    verification_id: str,
    request: VerificationCodeRequest,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    try:
        return runtime.verify(verification_id, request.code, user_id=context.user_id)
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.post("/verifications/{verification_id}/resend")
def resend_verification(
    verification_id: str,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    try:
        return runtime.resend_verification(verification_id, user_id=context.user_id)
    except (KeyError, PermissionError, ValueError) as error:
        raise _translate_error(error) from error


@router.post("/chat", deprecated=True)
def legacy_chat(
    request: CreateAgentRunRequest,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    return runtime.create_run(request.session_id, request.message, user_id=context.user_id)

