from fastapi import APIRouter, Depends

from backend.app.dependencies.auth import get_current_auth
from backend.app.services.agent_runtime import runtime
from backend.app.services.auth_service import AuthContext


router = APIRouter()


@router.get("")
def list_audit_logs(
    run_id: str | None = None,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    items = runtime.list_audits(run_id, user_id=context.user_id)
    return {"items": items, "total": len(items)}
