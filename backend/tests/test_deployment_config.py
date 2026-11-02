from scripts.validate_deployment_config import validate_environment


def _valid_environment() -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "DB_HOST": "mysql",
        "DB_NAME": "safebank_agent",
        "DB_USER": "safebank_app",
        "DB_PASSWORD": "database-password",
        "JWT_SECRET_KEY": "a" * 64,
        "MFA_OTP_PEPPER": "b" * 64,
        "FRAUD_RECIPIENT_PEPPER": "c" * 64,
        "CHANNEL_GATEWAY_SECRET": "d" * 64,
        "CHANNEL_IDENTITY_PEPPER": "e" * 64,
        "TRUSTED_APP_BASE_URL": "https://agent.bank.test",
        "AUTH_COOKIE_SECURE": "true",
        "DEEPSEEK_ENABLED": "true",
        "DEEPSEEK_API_KEY": "valid-api-key",
        "MYSQL_ROOT_PASSWORD": "root-database-password",
        "MYSQL_DATABASE": "safebank_agent",
        "MYSQL_USER": "safebank_app",
        "MYSQL_PASSWORD": "database-password",
        "APP_DOMAIN": "agent.bank.test",
    }


def test_valid_production_environment_passes() -> None:
    assert validate_environment(_valid_environment()) == []


def test_placeholders_and_reused_secrets_are_rejected() -> None:
    environment = _valid_environment()
    environment["DB_PASSWORD"] = "REPLACE_WITH_APP_DATABASE_PASSWORD"
    environment["MFA_OTP_PEPPER"] = environment["JWT_SECRET_KEY"]

    errors = validate_environment(environment)

    assert any("DB_PASSWORD" in error for error in errors)
    assert any("不能复用" in error for error in errors)


def test_http_requires_explicit_insecure_deployment_switch() -> None:
    environment = _valid_environment()
    environment["TRUSTED_APP_BASE_URL"] = "http://192.0.2.10"
    environment["AUTH_COOKIE_SECURE"] = "false"
    environment["APP_DOMAIN"] = ":80"

    errors = validate_environment(environment)

    assert any("DEPLOYMENT_ALLOW_INSECURE_HTTP" in error for error in errors)
    environment["DEPLOYMENT_ALLOW_INSECURE_HTTP"] = "true"
    assert validate_environment(environment) == []


def test_database_and_domain_mismatches_are_rejected() -> None:
    environment = _valid_environment()
    environment["MYSQL_PASSWORD"] = "different-password"
    environment["APP_DOMAIN"] = "other.bank.test"

    errors = validate_environment(environment)

    assert any("DB_PASSWORD" in error for error in errors)
    assert any("APP_DOMAIN" in error for error in errors)
