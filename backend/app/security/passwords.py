"""Argon2id 密码哈希与强度校验。"""

from __future__ import annotations

from pwdlib import PasswordHash


class PasswordPolicyError(ValueError):
    pass


class PasswordManager:
    def __init__(self) -> None:
        self._hasher = PasswordHash.recommended()
        self._dummy_hash = self._hasher.hash("SafeBank-Dummy-Password-Only-For-Timing")

    @staticmethod
    def validate_strength(password: str) -> None:
        if len(password) < 8 or len(password) > 128:
            raise PasswordPolicyError("密码长度必须为 8 至 128 个字符。")
        has_letter = any(character.isalpha() for character in password)
        has_digit = any(character.isdigit() for character in password)
        if not has_letter or not has_digit:
            raise PasswordPolicyError("密码必须同时包含字母和数字。")

    def hash(self, password: str) -> str:
        self.validate_strength(password)
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return self._hasher.verify(password, password_hash)
        except (TypeError, ValueError):
            return False

    def consume_dummy_verify(self, password: str) -> None:
        self.verify(password, self._dummy_hash)
