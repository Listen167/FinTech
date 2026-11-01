from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.db.models import Base, UserRecord
from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.repositories.memory import MemoryRepository
from backend.app.services.agent_runtime import AgentRuntime
from backend.app.services.life_graph_service import LifeGraphService


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def seed_user(engine, now: datetime) -> None:
    with Session(engine) as database, database.begin():
        database.add(UserRecord(
            id="user_001",
            display_name="林安",
            phone_masked="138****1024",
            role="CUSTOMER",
            status="ACTIVE",
            risk_level="LOW",
            created_at=now,
            updated_at=now,
        ))


def test_multiturn_life_event_creates_candidate_and_waits_for_confirmation(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")
    engine = build_engine()
    seed_user(engine, datetime(2026, 10, 29, 9, 0, 0))
    graph_repository = LifeGraphRepository(engine)
    graph_service = LifeGraphService(engine, graph_repository)
    runtime = AgentRuntime(MemoryRepository(), life_graph_service=graph_service)
    session_id = runtime.create_session(user_id="user_001")["session_id"]

    first = runtime.create_run(
        session_id,
        "我爱人下个月生日，帮我留1000元买礼物，提前两天提醒",
        user_id="user_001",
    )
    second = runtime.create_run(session_id, "18号", user_id="user_001")

    assert first["state"] == "WAITING_INPUT"
    assert first["data"]["missing_slots"] == ["event_date"]
    assert second["state"] == "WAITING_CONFIRMATION"
    assert second["intent"]["slots"]["event_date"].endswith("-18")
    assert second["pending_action"]["type"] == "LIFE_GRAPH_CHANGE_REQUEST"
    requests = graph_service.list_change_requests("user_001", status="PENDING")
    assert len(requests) == 1
    assert graph_service.get_graph("user_001")["nodes"] == []

    approved = graph_service.decide_change_request(
        owner_user_id="user_001",
        request_id=requests[0]["request_id"],
        approved=True,
    )
    graph = graph_service.get_graph("user_001")

    assert approved["status"] == "APPROVED"
    assert len(graph["nodes"]) == 4
    assert len(graph["edges"]) == 3
    assert graph["goals"][0]["target_amount"] == "1000.00"
    assert graph["actions"][0]["payload"]["days_before"] == 2
