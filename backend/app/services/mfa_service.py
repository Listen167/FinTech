"""Step-up MFA 验证码生成、摘要校验和本地演示投递箱。"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from backend.app.core.config import settings


class MfaCodeService:
    def __init__(self, pepper: str | None = None) -> None:
        configured = settings.mfa_otp_pepper or settings.jwt_secret_key
        self._pepper = (
            pepper
            or (configured.get_secret_value() if configured else None)
            or secrets.token_urlsafe(48)
        ).encode("utf-8")
        self._demo_outbox: dict[str, str] = {}

    def _digest(self, verification_id: str, code: str) -> str:
        payload = f"{verification_id}:{code}".encode("utf-8")
        return hmac.new(self._pepper, payload, hashlib.sha256).hexdigest()

    def issue(self, verification_id: str) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._demo_outbox[verification_id] = code
        return self._digest(verification_id, code)

    def matches(self, verification_id: str, code: str, expected_hash: str) -> bool:
        return hmac.compare_digest(self._digest(verification_id, code), expected_hash)

    def demo_code(self, verification_id: str) -> str | None:
        return self._demo_outbox.get(verification_id)

    def discard(self, verification_id: str) -> None:
        self._demo_outbox.pop(verification_id, None)

    def clear(self) -> None:
        self._demo_outbox.clear()
