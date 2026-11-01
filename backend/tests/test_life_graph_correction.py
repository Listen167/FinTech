from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Base,
    EventActionRecord,
    FinancialGoalRecord,
    LifeGraphChangeRequestRecord,
    UserRecord,
)
from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.schemas.life_graph import LifeGraphNodeCorrection
from backend.app.services.life_graph_proposal import build_life_graph_proposal
from backend.app.services.life_graph_service import LifeGraphService, LifeGraphServiceError


NOW = datetime(2026, 10, 29, 12, 0, 0)


def build_service() -> tuple[LifeGraphService, LifeGraphRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as database, database.begin():
        database.add_all([
            UserRecord(
                id="user_001",
                display_name="林安",
                phone_masked=None,
                role="CUSTOMER",
                status="ACTIVE",
                risk_level="LOW",
                created_at=NOW,
                updated_at=NOW,
            ),
            UserRecord(
                id="user_002",
                display_name="周宁",
                phone_masked=None,
                role="CUSTOMER",
                status="ACTIVE",
                risk_level="LOW",
                created_at=NOW,
                updated_at=NOW,
            ),
        ])
    repository = LifeGraphRepository(engine)
    service = LifeGraphService(engine, repository)
    change_set, summary = build_life_graph_proposal(
        repository,
        owner_user_id="user_001",
        slots={
            "related_person": "爱人",
            "relation_type": "SPOUSE_OF",
            "event_type": "BIRTHDAY",
            "event_label": "爱人生日",
            "event_date": "2026-11-18",
            "recurrence_rule": "FREQ=YEARLY",
            "goal_required": True,
            "goal_label": "爱人生日礼物心愿金",
            "target_amount": 1000,
            "currency": "CNY",
            "reminder_days_before": 2,
        },
    )
    request = service.create_change_request(
        owner_user_id="user_001",
        idempotency_key="seed-graph",
        summary=summary,
        change_set=change_set,
        now=NOW,
    )["request"]
    service.decide_change_request(
        owner_user_id="user_001",
        request_id=request["request_id"],
        approved=True,
        now=NOW,
    )
    return service, repository


def test_event_date_correction_reschedules_reminder_and_is_idempotent():
    service, repository = build_service()
    graph = repository.get_graph("user_001")
    event_id = graph["events"][0]["event_node_id"]
    correction = LifeGraphNodeCorrection(
        event_date="2026-12-01",
        reason="生日日期录入错误",
    )

    first = service.correct_node(
        owner_user_id="user_001",
        node_id=event_id,
        correction=correction,
        idempotency_key="correct-event-001",
        now=NOW,
    )
    replay = service.correct_node(
        owner_user_id="user_001",
        node_id=event_id,
        correction=correction,
        idempotency_key="correct-event-001",
        now=NOW,
    )

    assert first["correction_id"] == replay["correction_id"]
    assert first["graph"]["events"][0]["occurs_at"].startswith("2026-12-01")
    assert first["graph"]["actions"][0]["scheduled_at"].startswith("2026-11-29")
    with Session(service.engine) as database:
        corrections = database.scalars(
            select(LifeGraphChangeRequestRecord).where(
                LifeGraphChangeRequestRecord.source == "USER_CORRECTION"
            )
        ).all()
    assert len(corrections) == 1


def test_goal_correction_below_reserved_amount_is_rejected_and_rolled_back():
    service, repository = build_service()
    goal_id = repository.get_graph("user_001")["goals"][0]["goal_node_id"]
    with Session(service.engine) as database, database.begin():
        goal = database.get(FinancialGoalRecord, goal_id)
        goal.reserved_amount = Decimal("500.00")

    with pytest.raises(LifeGraphServiceError, match="TARGET_AMOUNT_BELOW_RESERVED"):
        service.correct_node(
            owner_user_id="user_001",
            node_id=goal_id,
            correction=LifeGraphNodeCorrection(
                target_amount="400.00",
                reason="尝试降低预算",
            ),
            idempotency_key="correct-goal-invalid",
            now=NOW,
        )

    assert repository.get_graph("user_001")["goals"][0]["target_amount"] == "1000.00"


def test_cross_user_correction_is_hidden_and_idempotency_conflict_is_explicit():
    service, repository = build_service()
    node_id = repository.get_graph("user_001")["nodes"][0]["node_id"]
    first = LifeGraphNodeCorrection(display_label="本人资料", reason="更新显示名称")
    service.correct_node(
        owner_user_id="user_001",
        node_id=node_id,
        correction=first,
        idempotency_key="correct-label-001",
        now=NOW,
    )

    with pytest.raises(LifeGraphServiceError, match="CORRECTION_IDEMPOTENCY_CONFLICT"):
        service.correct_node(
            owner_user_id="user_001",
            node_id=node_id,
            correction=LifeGraphNodeCorrection(display_label="另一个名称", reason="重复键"),
            idempotency_key="correct-label-001",
            now=NOW,
        )
    with pytest.raises(LifeGraphServiceError, match="LIFE_GRAPH_NODE_NOT_FOUND"):
        service.correct_node(
            owner_user_id="user_002",
            node_id=node_id,
            correction=first,
            idempotency_key="cross-user-correction",
            now=NOW,
        )
