"""交互式初始化两个演示账号；只将 Argon2id 哈希写入数据库。"""

from __future__ import annotations

from getpass import getpass

from backend.app.core.config import settings
from backend.app.db.database import create_database_engine
from backend.app.security.passwords import PasswordPolicyError
from backend.app.security.tokens import AccessTokenManager
from backend.app.services.auth_service import AuthService


def _read_password(label: str) -> str:
    password = getpass(f"请输入 {label} 的演示密码：")
    confirmation = getpass("请再次输入：")
    if password != confirmation:
        raise ValueError("两次输入的密码不一致。")
    return password


def main() -> None:
    if not settings.jwt_configured:
        raise SystemExit("请先在根目录 .env 中填写至少 32 个字符的 JWT_SECRET_KEY。")
    token_manager = AccessTokenManager(
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_minutes=settings.jwt_access_minutes,
    )
    service = AuthService(
        create_database_engine(),
        token_manager,
        max_failed_attempts=settings.auth_max_failed_attempts,
        lock_minutes=settings.auth_lock_minutes,
        session_days=settings.auth_session_days,
    )
    try:
        initialized = service.initialize_demo_credentials({
            "user_001": _read_password("lin_an（林安）"),
            "user_002": _read_password("zhou_ning（周宁）"),
        })
    except PasswordPolicyError as error:
        raise SystemExit(str(error)) from error
    for item in initialized:
        print(f"已初始化 {item['username']} -> {item['user_id']}")
    print("完成：数据库只保存 Argon2id 哈希，原始密码未写入文件。")


if __name__ == "__main__":
    main()
