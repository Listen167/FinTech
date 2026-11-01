"""生活事件 LangGraph 编排、恢复与人工接管测试。"""

from copy import deepcopy
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.db.models import Base, UserRecord
from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.repositories.memory import MemoryRepository
from backend.app.services.agent_runtime import AgentRuntime
from backend.app.services.life_graph_service import LifeGraphService, LifeGraphServiceError


def build_runtime() -> tuple[AgentRuntime, MemoryRepository, LifeGraphService]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 30, 9, 0, 0)
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
    repository = MemoryRepository()
    graph_repository = LifeGraphRepository(engine)
    service = LifeGraphService(engine, graph_repository)
    return AgentRuntime(repository, life_graph_service=service), repository, service


def create_complete_life_run(runtime: AgentRuntime) -> dict:
    session_id = runtime.create_session(user_id="user_001")["session_id"]
    return runtime.create_run(
        session_id,
        "我爱人11月18日生日，帮我留1000元买礼物，提前两天提醒",
        user_id="user_001",
    )


@pytest.fixture(autouse=True)
def disable_external_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")


def test_workflow_graph_contains_required_orchestration_nodes() -> None:
    runtime, _, _ = build_runtime()

    nodes = set(runtime.life_workflow.graph.get_graph().nodes)

    assert {
        "validate_slots",
        "await_input",
        "create_candidate",
        "analyze_conflicts",
        "await_confirmation",
        "apply_decision",
        "recover",
        "human_handoff",
    } <= nodes


def test_multiturn_run_records_langgraph_checkpoints() -> None:
    runtime, _, _ = build_runtime()
    session_id = runtime.create_session(user_id="user_001")["session_id"]

    first = runtime.create_run(
        session_id,
        "我爱人下个月生日，帮我留1000元买礼物",
        user_id="user_001",
    )
    first_checkpoint = deepcopy(first["workflow"])
    second = runtime.create_run(session_id, "18号", user_id="user_001")

    assert first_checkpoint["engine"] == "langgraph"
    assert first_checkpoint["stage"] == "WAITING_INPUT"
    assert first_checkpoint["trace"] == ["validate_slots", "await_input"]
    assert runtime.get_run(first["run_id"], user_id="user_001")["workflow"]["stage"] == "RESOLVED"
    assert second["workflow"]["stage"] == "WAITING_CONFIRMATION"
    assert second["workflow"]["trace"] == [
        "validate_slots",
        "create_candidate",
        "analyze_conflicts",
        "await_confirmation",
    ]


def test_restart_recovers_pending_confirmation_without_duplicate_candidate() -> None:
    runtime, repository, service = build_runtime()
    run = create_complete_life_run(runtime)
    request_id = run["workflow"]["change_request_id"]

    recovered_runtime = AgentRuntime(repository, life_graph_service=service)
    recovered = recovered_runtime.get_run(run["run_id"], user_id="user_001")

    assert recovered["state"] == "WAITING_CONFIRMATION"
    assert recovered["workflow"]["recovered"] is True
    assert recovered["workflow"]["trace"][-1] == "recover"
    assert recovered["pending_action"]["life_graph_change_request"]["request_id"] == request_id
    assert len(service.list_change_requests("user_001", status="PENDING")) == 1


def test_restart_observes_decision_completed_while_runtime_was_offline() -> None:
    runtime, repository, service = build_runtime()
    run = create_complete_life_run(runtime)
    request_id = run["workflow"]["change_request_id"]
    service.decide_change_request(
        owner_user_id="user_001",
        request_id=request_id,
        approved=True,
    )

    recovered_runtime = AgentRuntime(repository, life_graph_service=service)
    recovered = recovered_runtime.get_run(run["run_id"], user_id="user_001")

    assert recovered["state"] == "SUCCEEDED"
    assert recovered["workflow"]["stage"] == "COMPLETED"
    assert recovered["pending_action"] == {"type": "NONE"}
    assert all(
        node["runtime"]["status"] == "SUCCEEDED"
        for node in recovered["plan"]["nodes"]
    )


def test_restart_marks_rejected_offline_candidate_nodes_as_skipped() -> None:
    runtime, repository, service = build_runtime()
    run = create_complete_life_run(runtime)
    request_id = run["workflow"]["change_request_id"]
    service.decide_change_request(
        owner_user_id="user_001",
        request_id=request_id,
        approved=False,
        reason="用户在其他受信入口拒绝",
    )

    recovered_runtime = AgentRuntime(repository, life_graph_service=service)
    recovered = recovered_runtime.get_run(run["run_id"], user_id="user_001")
    statuses = [node["runtime"]["status"] for node in recovered["plan"]["nodes"]]

    assert recovered["state"] == "ABORTED"
    assert statuses[:3] == ["SUCCEEDED", "SUCCEEDED", "SUCCEEDED"]
    assert statuses[3:] == ["SKIPPED", "SKIPPED"]
    assert service.get_graph("user_001")["nodes"] == []


def test_life_graph_approval_is_executed_by_the_final_task_node() -> None:
    runtime, _, service = build_runtime()
    run = create_complete_life_run(runtime)
    request_id = run["workflow"]["change_request_id"]

    result = runtime.decide_life_graph_change(
        request_id=request_id,
        approved=True,
        reason="用户确认候选方案",
        user_id="user_001",
        service=service,
    )
    completed = runtime.get_run(run["run_id"], user_id="user_001")

    assert result["status"] == "APPROVED"
    assert completed["state"] == "SUCCEEDED"
    assert completed["plan"]["nodes"][-1]["tool"] == "life_graph.change_request.decide"
    assert completed["plan"]["nodes"][-1]["runtime"]["status"] == "SUCCEEDED"
    assert completed["plan"]["nodes"][-1]["runtime"]["output_receipt"]["reference_id"] == request_id
    assert len(service.get_graph("user_001")["goals"]) == 1


def test_human_handoff_freezes_workflow_and_rejects_pending_candidate() -> None:
    runtime, _, service = build_runtime()
    run = create_complete_life_run(runtime)
    request_id = run["workflow"]["change_request_id"]

    handed_off = runtime.request_human_handoff(
        run["run_id"],
        user_id="user_001",
        reason="需要人工核对家庭目标",
    )

    assert handed_off["state"] == "WAITING_HUMAN"
    assert handed_off["workflow"]["stage"] == "WAITING_HUMAN"
    assert handed_off["pending_action"] == {"type": "NONE"}
    assert handed_off["data"]["human_handoff"]["candidate_status"] == "REJECTED"
    assert service.get_change_request("user_001", request_id)["status"] == "REJECTED"
    assert service.get_graph("user_001")["nodes"] == []
    with pytest.raises(PermissionError, match="RESOURCE_FORBIDDEN"):
        runtime.request_human_handoff(
            run["run_id"],
            user_id="user_002",
            reason="越权接管",
        )


def test_abort_rejects_life_candidate_and_prevents_later_confirmation() -> None:
    runtime, _, service = build_runtime()
    run = create_complete_life_run(runtime)
    request_id = run["workflow"]["change_request_id"]

    aborted = runtime.abort(run["run_id"], user_id="user_001")

    assert aborted["state"] == "ABORTED"
    assert aborted["workflow"]["stage"] == "ABORTED"
    assert service.get_change_request("user_001", request_id)["status"] == "REJECTED"
    with pytest.raises(LifeGraphServiceError, match="CHANGE_REQUEST_REPLAYED"):
        service.decide_change_request(
            owner_user_id="user_001",
            request_id=request_id,
            approved=True,
        )


def test_missing_slot_checkpoint_restores_session_pending_context() -> None:
    runtime, repository, service = build_runtime()
    session_id = runtime.create_session(user_id="user_001")["session_id"]
    run = runtime.create_run(
        session_id,
        "我爱人下个月生日，帮我留1000元买礼物",
        user_id="user_001",
    )
    repository.sessions[session_id]["pending_intent"] = None
    repository.sessions[session_id]["pending_run_id"] = None
    repository.sessions[session_id]["pending_expires_at"] = None

    recovered_runtime = AgentRuntime(repository, life_graph_service=service)
    session = recovered_runtime.get_session(session_id, user_id="user_001")

    assert session["pending"]["run_id"] == run["run_id"]
    assert session["pending"]["missing_slots"] == ["event_date"]
    assert recovered_runtime.get_run(run["run_id"], user_id="user_001")["workflow"]["recovered"] is True


def test_unconfigured_graph_service_fails_closed_without_business_write() -> None:
    runtime = AgentRuntime(MemoryRepository())
    session_id = runtime.create_session(user_id="user_001")["session_id"]

    run = runtime.create_run(
        session_id,
        "我爱人11月18日生日，帮我留1000元买礼物",
        user_id="user_001",
    )

    assert run["state"] == "FAILED"
    assert run["workflow"]["stage"] == "FAILED"
    assert run["workflow"]["error_code"] == "LIFE_GRAPH_NOT_CONFIGURED"
    assert run["pending_action"] == {"type": "NONE"}
