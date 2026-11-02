"""生产部署前置校验；只报告字段名，不输出任何密钥值。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit


REQUIRED_FIELDS = (
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "JWT_SECRET_KEY",
    "MFA_OTP_PEPPER",
    "FRAUD_RECIPIENT_PEPPER",
    "CHANNEL_GATEWAY_SECRET",
    "CHANNEL_IDENTITY_PEPPER",
    "TRUSTED_APP_BASE_URL",
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
)
SECRET_FIELDS = (
    "JWT_SECRET_KEY",
    "MFA_OTP_PEPPER",
    "FRAUD_RECIPIENT_PEPPER",
    "CHANNEL_GATEWAY_SECRET",
    "CHANNEL_IDENTITY_PEPPER",
)
TRUE_VALUES = {"1", "true", "yes", "on"}


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def _missing_or_placeholder(value: str | None) -> bool:
    normalized = (value or "").strip()
    return not normalized or "REPLACE_WITH" in normalized.upper()


def validate_environment(environment: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    if environment.get("ENVIRONMENT", "").strip().lower() != "production":
        errors.append("ENVIRONMENT 必须设置为 production。")

    for field in REQUIRED_FIELDS:
        if _missing_or_placeholder(environment.get(field)):
            errors.append(f"{field} 未填写或仍是模板占位值。")

    for field in SECRET_FIELDS:
        value = environment.get(field, "").strip()
        if value and not _missing_or_placeholder(value) and len(value) < 32:
            errors.append(f"{field} 至少需要 32 个字符。")

    configured_secrets = [
        environment[field].strip()
        for field in SECRET_FIELDS
        if field in environment and not _missing_or_placeholder(environment[field])
    ]
    if len(configured_secrets) != len(set(configured_secrets)):
        errors.append("JWT、MFA、反诈和渠道密钥必须分别生成，不能复用。")

    if _enabled(environment.get("DEEPSEEK_ENABLED")) and _missing_or_placeholder(
        environment.get("DEEPSEEK_API_KEY")
    ):
        errors.append("DEEPSEEK_ENABLED=true 时必须填写 DEEPSEEK_API_KEY。")

    if environment.get("DB_NAME", "").strip() != environment.get(
        "MYSQL_DATABASE", ""
    ).strip():
        errors.append("DB_NAME 必须与 MYSQL_DATABASE 完全一致。")
    if environment.get("DB_USER", "").strip() != environment.get(
        "MYSQL_USER", ""
    ).strip():
        errors.append("DB_USER 必须与 MYSQL_USER 完全一致。")
    if environment.get("DB_PASSWORD", "").strip() != environment.get(
        "MYSQL_PASSWORD", ""
    ).strip():
        errors.append("DB_PASSWORD 必须与 MYSQL_PASSWORD 完全一致。")
    if environment.get("MYSQL_ROOT_PASSWORD", "").strip() == environment.get(
        "MYSQL_PASSWORD", ""
    ).strip():
        errors.append("MYSQL_ROOT_PASSWORD 不能与应用数据库密码相同。")

    trusted_url = environment.get("TRUSTED_APP_BASE_URL", "").strip()
    if trusted_url and not _missing_or_placeholder(trusted_url):
        parsed = urlsplit(trusted_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("TRUSTED_APP_BASE_URL 必须是完整的 HTTP(S) 地址。")
        elif parsed.query or parsed.fragment:
            errors.append("TRUSTED_APP_BASE_URL 不能包含查询参数或片段。")
        elif parsed.hostname and parsed.hostname.endswith("example.com"):
            errors.append("TRUSTED_APP_BASE_URL 仍是示例域名，请替换为真实地址。")
        elif parsed.scheme == "https" and not _enabled(
            environment.get("AUTH_COOKIE_SECURE")
        ):
            errors.append("HTTPS 部署必须设置 AUTH_COOKIE_SECURE=true。")
        elif parsed.scheme == "http":
            if not _enabled(environment.get("DEPLOYMENT_ALLOW_INSECURE_HTTP")):
                errors.append(
                    "HTTP 仅允许临时联调；如确需使用，请显式设置 "
                    "DEPLOYMENT_ALLOW_INSECURE_HTTP=true。"
                )
            if _enabled(environment.get("AUTH_COOKIE_SECURE")):
                errors.append(
                    "HTTP 临时联调必须设置 AUTH_COOKIE_SECURE=false，否则浏览器不会携带刷新 Cookie。"
                )

        app_domain = environment.get("APP_DOMAIN", "").strip()
        if app_domain and app_domain != ":80":
            if "://" in app_domain or "/" in app_domain:
                errors.append("APP_DOMAIN 只能填写主机名，不能包含协议、端口或路径。")
            elif parsed.hostname != app_domain:
                errors.append("APP_DOMAIN 必须与 TRUSTED_APP_BASE_URL 的主机名一致。")
        elif app_domain == ":80" and parsed.scheme != "http":
            errors.append("APP_DOMAIN=:80 临时联调时 TRUSTED_APP_BASE_URL 必须使用 HTTP。")

    return errors


def main() -> None:
    errors = validate_environment(os.environ)
    if errors:
        details = "\n".join(f"- {item}" for item in errors)
        raise SystemExit(f"生产部署配置校验失败：\n{details}")
    print("生产部署配置校验通过。")


if __name__ == "__main__":
    main()
