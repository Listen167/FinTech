"""认证服务和当前用户依赖。"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import settings
from backend.app.repositories.memory import repository
from backend.app.security.tokens import AccessTokenManager
from backend.app.services.auth_service import AuthContext, AuthenticationError, AuthService


bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def get_auth_service() -> AuthService:
    engine = getattr(repository, "engine", None)
    if engine is None or not settings.jwt_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "认证服务尚未完成配置。"},
        )
    token_manager = AccessTokenManager(
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_minutes=settings.jwt_access_minutes,
    )
    return AuthService(
        engine=engine,
        token_manager=token_manager,
        max_failed_attempts=settings.auth_max_failed_attempts,
        lock_minutes=settings.auth_lock_minutes,
        session_days=settings.auth_session_days,
    )


def get_current_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AuthService = Depends(get_auth_service),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "NOT_AUTHENTICATED", "message": "请先登录。"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return service.validate_access_token(
            credentials.credentials,
            request.client.host if request.client else None,
        )
    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_ACCESS_TOKEN", "message": "登录已失效，请重新登录。"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
