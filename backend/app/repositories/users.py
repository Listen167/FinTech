"""用户身份仓库；认证服务通过这里读取用户，不直接信任请求参数。"""

from __future__ import annotations

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.app.db.models import UserRecord


class UserRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_by_id(self, user_id: str) -> UserRecord | None:
        with Session(self.engine) as session:
            return session.get(UserRecord, user_id)

    def list_demo_users(self) -> list[dict]:
        with Session(self.engine) as session:
            users = session.scalars(select(UserRecord).order_by(UserRecord.id)).all()
            return [
                {
                    "id": user.id,
                    "display_name": user.display_name,
                    "phone_masked": user.phone_masked,
                    "role": user.role,
                    "status": user.status,
                    "risk_level": user.risk_level,
                }
                for user in users
            ]
