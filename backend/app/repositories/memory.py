"""比赛沙箱使用的内存仓库，可一键恢复演示数据。"""

from __future__ import annotations

from copy import deepcopy

from backend.app.mock_bank.seed import (
    DEMO_ACCOUNTS,
    DEMO_CONTACTS,
    DEMO_SUBSCRIPTIONS,
    build_demo_transactions,
)


class MemoryRepository:
    backend = "memory"
    persistent = False

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.accounts = deepcopy(DEMO_ACCOUNTS)
        self.contacts = deepcopy(DEMO_CONTACTS)
        self.subscriptions = deepcopy(DEMO_SUBSCRIPTIONS)
        self.transactions = build_demo_transactions()
        self.runs: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.confirmations: dict[str, dict] = {}
        self.verifications: dict[str, dict] = {}
        self.previews: dict[str, dict] = {}
        self.audit_logs: list[dict] = []
        self.idempotency: dict[tuple[str, str], str] = {}

    def flush(self) -> None:
        """内存仓库无需同步；持久化仓库覆盖此方法。"""

    def persistence_status(self) -> dict:
        status = {
            "backend": self.backend,
            "requested_backend": getattr(self, "requested_backend", self.backend),
            "persistent": self.persistent,
            "connected": True,
        }
        if hasattr(self, "persistence_error"):
            status["fallback_reason"] = self.persistence_error
        return status


def _create_repository() -> MemoryRepository:
    from backend.app.core.config import settings

    if settings.database_enabled and settings.database_configured:
        try:
            from backend.app.db.database import create_database_engine
            from backend.app.repositories.mysql import MySQLRepository

            return MySQLRepository(create_database_engine())
        except Exception as error:
            fallback = MemoryRepository()
            fallback.requested_backend = "mysql"
            fallback.persistence_error = type(error).__name__
            return fallback
    return MemoryRepository()


repository = _create_repository()
