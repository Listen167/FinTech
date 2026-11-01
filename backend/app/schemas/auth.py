"""认证接口请求与响应模型。"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


class AuthenticatedUserResponse(BaseModel):
    id: str
    display_name: str
    phone_masked: str | None
    role: str
    risk_level: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: AuthenticatedUserResponse


class LogoutResponse(BaseModel):
    success: bool = True


class LogoutAllResponse(LogoutResponse):
    revoked_sessions: int
