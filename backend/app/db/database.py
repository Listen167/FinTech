"""数据库连接工厂，避免在日志或异常状态中暴露密码。"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL

from backend.app.core.config import settings


def build_database_url() -> URL:
    if not settings.database_configured:
        raise RuntimeError("DATABASE_NOT_CONFIGURED")
    return URL.create(
        drivername="mysql+pymysql",
        username=settings.db_user,
        password=settings.db_password.get_secret_value() if settings.db_password else None,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        query={"charset": "utf8mb4"},
    )


def create_database_engine() -> Engine:
    return create_engine(
        build_database_url(),
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )
