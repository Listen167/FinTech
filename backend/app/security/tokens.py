"""严格校验声明和算法的短期 JWT Access Token。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt


REQUIRED_ACCESS_CLAIMS = (
    "sub",
    "sid",
    "jti",
    "iss",
    "aud",
    "iat",
    "nbf",
    "exp",
    "token_type",
    "credential_version",
)


class AccessTokenManager:
    def __init__(
        self,
        secret: str,
        algorithm: str,
        issuer: str,
        audience: str,
        access_minutes: int,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_TOO_SHORT")
        if algorithm != "HS256":
            raise ValueError("JWT_ALGORITHM_NOT_ALLOWED")
        if not 1 <= access_minutes <= 30:
            raise ValueError("JWT_ACCESS_TTL_OUT_OF_RANGE")
        self.secret = secret
        self.algorithm = algorithm
        self.issuer = issuer
        self.audience = audience
        self.access_minutes = access_minutes

    @property
    def expires_in_seconds(self) -> int:
        return self.access_minutes * 60

    def create(
        self,
        user_id: str,
        session_id: str,
        credential_version: int,
        now: datetime | None = None,
    ) -> str:
        issued_at = now or datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "sid": session_id,
            "jti": f"jti_{uuid4().hex}",
            "iss": self.issuer,
            "aud": self.audience,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + timedelta(minutes=self.access_minutes),
            "token_type": "access",
            "credential_version": credential_version,
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def decode(self, token: str) -> dict:
        payload = jwt.decode(
            token,
            self.secret,
            algorithms=[self.algorithm],
            issuer=self.issuer,
            audience=self.audience,
            options={"require": list(REQUIRED_ACCESS_CLAIMS)},
        )
        if payload.get("token_type") != "access":
            raise jwt.InvalidTokenError("invalid token type")
        if not isinstance(payload.get("credential_version"), int):
            raise jwt.InvalidTokenError("invalid credential version")
        return payload
