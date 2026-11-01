"""操作护照读取与脱敏 JSON 导出接口。"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from backend.app.dependencies.auth import get_current_auth
from backend.app.errors import error_definition
from backend.app.services.agent_runtime import runtime
from backend.app.services.auth_service import AuthContext


router = APIRouter()


def _passport(run_id: str, user_id: str) -> dict:
    try:
        return runtime.get_operation_passport(run_id, user_id=user_id)
    except (KeyError, PermissionError, ValueError) as error:
        code = str(error.args[0]) if error.args else "INTERNAL_ERROR"
        definition = error_definition(code)
        raise HTTPException(
            status_code=definition.status_code,
            detail={
                "code": code,
                "message": definition.message,
                "retryable": definition.retryable,
            },
        ) from error


@router.get("/runs/{run_id}")
def get_operation_passport(
    run_id: str,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    return _passport(run_id, context.user_id)


@router.get("/runs/{run_id}/export", response_class=JSONResponse)
def export_operation_passport(
    run_id: str,
    context: AuthContext = Depends(get_current_auth),
) -> JSONResponse:
    passport = _passport(run_id, context.user_id)
    return JSONResponse(
        content=passport,
        headers={
            "Content-Disposition": f'attachment; filename="operation-passport-{run_id}.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
