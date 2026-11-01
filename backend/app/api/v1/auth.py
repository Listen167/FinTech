"""密码登录、Token 轮换、当前用户和注销接口。"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from backend.app.core.config import settings
from backend.app.dependencies.auth import get_auth_service, get_current_auth
from backend.app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutAllResponse,
    LogoutResponse,
)
from backend.app.services.auth_service import AuthContext, AuthenticationError, AuthService


router = APIRouter()
AUTH_COOKIE_PATH = "/api/auth"
LEGACY_AUTH_COOKIE_PATH = "/api/v1/auth"


def _set_auth_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    max_age = settings.auth_session_days * 24 * 60 * 60
    response.delete_cookie(settings.auth_refresh_cookie_name, path=LEGACY_AUTH_COOKIE_PATH)
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path=AUTH_COOKIE_PATH,
    )
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.auth_refresh_cookie_name, path=AUTH_COOKIE_PATH)
    response.delete_cookie(settings.auth_refresh_cookie_name, path=LEGACY_AUTH_COOKIE_PATH)
    response.delete_cookie(settings.auth_csrf_cookie_name, path="/")


@router.get("/status")
def auth_status() -> dict:
    return {
        "configured": settings.jwt_configured,
        "algorithm": settings.jwt_algorithm,
        "issuer": settings.jwt_issuer,
        "audience": settings.jwt_audience,
        "access_minutes": settings.jwt_access_minutes,
        "refresh_enabled": True,
    }


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> dict:
    try:
        result = service.authenticate(
            payload.username,
            payload.password,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
        _set_auth_cookies(
            response,
            result.pop("_refresh_token"),
            result.pop("_csrf_token"),
        )
        return result
    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "用户名或密码错误，或账号暂时不可用。",
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


@router.post("/refresh", response_model=LoginResponse)
def refresh(
    request: Request,
    response: Response,
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    service: AuthService = Depends(get_auth_service),
) -> dict | Response:
    refresh_token = request.cookies.get(settings.auth_refresh_cookie_name)
    if not refresh_token or not csrf_token:
        error_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": {
                    "code": "REFRESH_CREDENTIALS_MISSING",
                    "message": "刷新凭据缺失，请重新登录。",
                }
            },
        )
        _clear_auth_cookies(error_response)
        return error_response
    try:
        result = service.refresh_access_token(
            refresh_token,
            csrf_token,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
    except AuthenticationError:
        error_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": {
                    "code": "INVALID_REFRESH_TOKEN",
                    "message": "刷新凭据无效，请重新登录。",
                }
            },
        )
        _clear_auth_cookies(error_response)
        return error_response
    _set_auth_cookies(
        response,
        result.pop("_refresh_token"),
        result.pop("_csrf_token"),
    )
    return result


@router.get("/me")
def current_user(context: AuthContext = Depends(get_current_auth)) -> dict:
    return context.public_user()


@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    request: Request,
    context: AuthContext = Depends(get_current_auth),
    service: AuthService = Depends(get_auth_service),
) -> dict:
    service.logout(context, request.client.host if request.client else None)
    _clear_auth_cookies(response)
    return {"success": True}


@router.post("/logout-all", response_model=LogoutAllResponse)
def logout_all(
    response: Response,
    request: Request,
    context: AuthContext = Depends(get_current_auth),
    service: AuthService = Depends(get_auth_service),
) -> dict:
    revoked_sessions = service.logout_all(
        context, request.client.host if request.client else None
    )
    _clear_auth_cookies(response)
    return {"success": True, "revoked_sessions": revoked_sessions}
