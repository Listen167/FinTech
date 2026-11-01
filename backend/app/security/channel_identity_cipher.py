"""渠道投递标识加密；HMAC 用于查重，密文只供内部主动投递。"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from backend.app.errors import ApplicationError


class ChannelIdentityCipher:
    def __init__(self, pepper: str) -> None:
        if len(pepper) < 32:
            raise ValueError("CHANNEL_IDENTITY_PEPPER_TOO_SHORT")
        key = hashlib.sha256(b"delivery-id\0" + pepper.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, external_user_id: str) -> str:
        return self._fernet.encrypt(external_user_id.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> str:
        if not ciphertext:
            raise ApplicationError("FEISHU_DELIVERY_ID_UNAVAILABLE")
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as error:
            raise ApplicationError("FEISHU_DELIVERY_ID_UNAVAILABLE") from error
