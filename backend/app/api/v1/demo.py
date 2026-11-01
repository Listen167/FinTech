from fastapi import APIRouter, Depends, HTTPException, Response

from backend.app.core.config import settings
from backend.app.dependencies.auth import get_current_auth
from backend.app.repositories.memory import repository
from backend.app.services.agent_runtime import runtime
from backend.app.services.auth_service import AuthContext
from backend.app.services.guardian_circle import GuardianCircleService


router = APIRouter()


@router.post("/reset")
def reset_demo(
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    if settings.environment != "local":
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    runtime.reset_demo(user_id=context.user_id)
    engine = getattr(repository, "engine", None)
    if engine is not None:
        GuardianCircleService(
            engine,
            invite_ttl_seconds=settings.guardian_invite_ttl_seconds,
        ).reset_user(context.user_id)
    return {"success": True, "message": "演示数据已恢复。"}


@router.get("/verifications/{verification_id}/code")
def get_demo_verification_code(
    verification_id: str,
    response: Response,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    if settings.environment != "local":
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    response.headers["Cache-Control"] = "no-store"
    try:
        return runtime.get_demo_verification_code(
            verification_id, user_id=context.user_id
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"code": str(error.args[0])}) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail={"code": str(error.args[0])}) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail={"code": str(error.args[0])}) from error


@router.get("/fraud-release-challenges/{challenge_id}/code")
def get_demo_fraud_release_code(
    challenge_id: str,
    response: Response,
    context: AuthContext = Depends(get_current_auth),
) -> dict:
    if settings.environment != "local":
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    response.headers["Cache-Control"] = "no-store"
    return runtime.fraud_cooling.get_demo_code(
        owner_user_id=context.user_id,
        challenge_id=challenge_id,
    )
