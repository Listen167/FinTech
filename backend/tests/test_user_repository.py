from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.models import Base, UserRecord
from backend.app.repositories.users import UserRepository


def test_two_demo_users_are_isolated_records():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 27, 20, 0, 0)
    with Session(engine) as session, session.begin():
        session.add_all([
            UserRecord(
                id="user_001",
                display_name="林安",
                phone_masked="138****1024",
                role="CUSTOMER",
                status="ACTIVE",
                risk_level="LOW",
                created_at=now,
                updated_at=now,
            ),
            UserRecord(
                id="user_002",
                display_name="周宁",
                phone_masked="139****2048",
                role="CUSTOMER",
                status="ACTIVE",
                risk_level="LOW",
                created_at=now,
                updated_at=now,
            ),
        ])

    repository = UserRepository(engine)
    users = repository.list_demo_users()

    assert [user["id"] for user in users] == ["user_001", "user_002"]
    assert users[0]["phone_masked"] != users[1]["phone_masked"]
    assert repository.get_by_id("user_001").display_name == "林安"
