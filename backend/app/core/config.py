from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from urllib.parse import urlsplit


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_name: str = "安心办 SafeBank Guardian API"
    api_version: str = "0.3.0"
    environment: str = "local"
    database_enabled: bool = True
    db_host: str | None = None
    db_port: int = 3306
    db_name: str | None = None
    db_user: str | None = None
    db_password: SecretStr | None = None
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "safebank-agent"
    jwt_audience: str = "safebank-web"
    jwt_access_minutes: int = 10
    auth_max_failed_attempts: int = 5
    auth_lock_minutes: int = 10
    auth_session_days: int = 7
    auth_refresh_cookie_name: str = "safebank_refresh"
    auth_csrf_cookie_name: str = "safebank_csrf"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    mfa_otp_pepper: SecretStr | None = None
    mfa_code_ttl_seconds: int = Field(default=300, ge=60, le=600)
    mfa_max_attempts: int = Field(default=3, ge=3, le=5)
    mfa_resend_cooldown_seconds: int = Field(default=30, ge=10, le=120)
    channel_gateway_secret: SecretStr | None = None
    channel_identity_pepper: SecretStr | None = None
    channel_link_token_ttl_seconds: int = Field(default=600, ge=120, le=1800)
    channel_gateway_base_url: str = "http://127.0.0.1:8000"
    channel_gateway_timeout_seconds: int = Field(default=60, ge=5, le=120)
    channel_worker_heartbeat_seconds: int = Field(default=20, ge=5, le=120)
    channel_worker_lease_seconds: int = Field(default=75, ge=30, le=300)
    trusted_app_base_url: str = "http://127.0.0.1:5173"
    feishu_app_id: str | None = None
    feishu_app_secret: SecretStr | None = None
    feishu_verification_token: SecretStr | None = None
    feishu_encrypt_key: SecretStr | None = None
    feishu_webhook_max_skew_seconds: int = Field(default=300, ge=60, le=900)
    feishu_worker_threads: int = Field(default=1, ge=1, le=4)
    feishu_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    life_radar_poll_seconds: int = Field(default=30, ge=5, le=3600)
    life_radar_batch_size: int = Field(default=20, ge=1, le=100)
    life_radar_lease_seconds: int = Field(default=120, ge=30, le=1800)
    life_radar_max_attempts: int = Field(default=3, ge=1, le=10)
    life_radar_retry_base_seconds: int = Field(default=60, ge=10, le=3600)
    life_radar_simulation_enabled: bool = False
    fraud_recipient_pepper: SecretStr | None = None
    fraud_cooling_minutes: int = Field(default=30, ge=1, le=1440)
    fraud_release_challenge_ttl_seconds: int = Field(default=300, ge=60, le=600)
    fraud_release_max_attempts: int = Field(default=3, ge=3, le=5)
    guardian_invite_ttl_seconds: int = Field(default=600, ge=60, le=86400)

    @field_validator("mfa_otp_pepper", mode="before")
    @classmethod
    def validate_mfa_otp_pepper(cls, value):
        if value is None or value == "":
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        if len(raw_value) < 32:
            raise ValueError("MFA_OTP_PEPPER 至少需要 32 个字符")
        return value

    @field_validator("channel_gateway_secret", "channel_identity_pepper", mode="before")
    @classmethod
    def validate_channel_secrets(cls, value):
        if value is None or value == "":
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        if len(raw_value) < 32:
            raise ValueError("Channel Gateway 密钥至少需要 32 个字符")
        return value

    @field_validator(
        "feishu_app_secret",
        "feishu_verification_token",
        "feishu_encrypt_key",
        mode="before",
    )
    @classmethod
    def empty_feishu_secret_as_none(cls, value):
        if value is None or value == "":
            return None
        return value

    @field_validator("fraud_recipient_pepper", mode="before")
    @classmethod
    def validate_fraud_recipient_pepper(cls, value):
        if value is None or value == "":
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        if len(raw_value) < 32:
            raise ValueError("FRAUD_RECIPIENT_PEPPER 至少需要 32 个字符")
        return value

    @field_validator("trusted_app_base_url")
    @classmethod
    def validate_trusted_app_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("TRUSTED_APP_BASE_URL 必须是完整的 HTTP(S) 地址")
        if parsed.query or parsed.fragment:
            raise ValueError("TRUSTED_APP_BASE_URL 不能包含查询参数或片段")
        return value.rstrip("/")

    @property
    def database_configured(self) -> bool:
        return all((self.db_host, self.db_name, self.db_user, self.db_password))

    @property
    def jwt_configured(self) -> bool:
        if not self.jwt_secret_key:
            return False
        return len(self.jwt_secret_key.get_secret_value()) >= 32

    @property
    def channel_gateway_configured(self) -> bool:
        return bool(self.channel_gateway_secret and self.channel_identity_pepper)

    @property
    def feishu_configured(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret)

    @property
    def feishu_webhook_configured(self) -> bool:
        return bool(
            self.channel_gateway_configured
            and self.feishu_configured
            and self.feishu_verification_token
            and self.feishu_encrypt_key
        )

    @property
    def life_radar_configured(self) -> bool:
        return bool(
            self.database_configured
            and self.channel_gateway_configured
            and self.feishu_configured
        )


settings = Settings()

