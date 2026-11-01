from sqlalchemy import create_engine

from backend.app.db.models import Base
from backend.app.repositories.mysql import MySQLRepository
from backend.app.services.agent_runtime import AgentRuntime


def test_pending_conversation_survives_repository_reload(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    first_repository = MySQLRepository(engine)
    first_runtime = AgentRuntime(first_repository)
    session_id = first_runtime.create_session(user_id="user_001")["session_id"]
    first = first_runtime.create_run(
        session_id, "给小王转账", "persistent-message-1", user_id="user_001"
    )
    assert first["state"] == "WAITING_INPUT"

    reloaded_repository = MySQLRepository(engine)
    reloaded_runtime = AgentRuntime(reloaded_repository)
    restored = reloaded_runtime.get_session(session_id, user_id="user_001")
    assert restored["pending"]["missing_slots"] == ["amount"]

    second = reloaded_runtime.create_run(
        session_id, "200 元", "persistent-message-2", user_id="user_001"
    )
    assert second["state"] == "WAITING_CONFIRMATION"
    assert second["intent"]["slots"]["recipient"] == "小王"
    assert second["intent"]["slots"]["amount"] == 200.0

    final_repository = MySQLRepository(engine)
    assert len(final_repository.sessions[session_id]["run_ids"]) == 2
    assert final_repository.runs[first["run_id"]]["state"] == "RESOLVED"


def test_pending_authorization_and_task_graph_survive_repository_reload(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    first_repository = MySQLRepository(engine)
    first_runtime = AgentRuntime(first_repository)
    session_id = first_runtime.create_session(user_id="user_001")["session_id"]
    waiting = first_runtime.create_run(
        session_id,
        "取消云盘会员",
        "persistent-confirmation-1",
        user_id="user_001",
    )
    confirmation_id = waiting["pending_action"]["confirmation"]["confirmation_id"]

    reloaded_repository = MySQLRepository(engine)
    reloaded_runtime = AgentRuntime(reloaded_repository)
    completed = reloaded_runtime.decide_confirmation(
        confirmation_id,
        True,
        user_id="user_001",
    )

    assert completed["state"] == "SUCCEEDED"
    assert completed["data"]["subscription"]["status"] == "CANCELED"
    assert all(
        node["runtime"]["status"] == "SUCCEEDED"
        for node in completed["plan"]["nodes"]
    )
