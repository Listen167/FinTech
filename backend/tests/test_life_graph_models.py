from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Base,
    EventActionRecord,
    FinancialGoalRecord,
    LifeEventRecord,
    LifeGraphEdgeRecord,
    LifeGraphNodeRecord,
    UserRecord,
)


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def seed_users(database: Session, now: datetime) -> None:
    database.add_all([
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


def test_life_graph_supports_multiple_relationship_contexts_and_scheduled_goal():
    engine = build_engine()
    now = datetime(2026, 10, 28, 10, 0, 0)
    birthday = datetime(2026, 11, 18, 0, 0, 0)
    reminder_at = datetime(2026, 11, 16, 9, 0, 0)

    with Session(engine) as database, database.begin():
        seed_users(database, now)
        database.flush()
        database.add_all([
            LifeGraphNodeRecord(
                node_id="person_self_001",
                owner_user_id="user_001",
                node_type="PERSON",
                display_label="本人",
                sensitivity_level="PERSONAL",
                status="ACTIVE",
                attributes={"is_account_owner": True},
                created_at=now,
                updated_at=now,
            ),
            LifeGraphNodeRecord(
                node_id="person_spouse_001",
                owner_user_id="user_001",
                node_type="PERSON",
                display_label="爱人",
                sensitivity_level="SENSITIVE",
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
            LifeGraphNodeRecord(
                node_id="event_birthday_001",
                owner_user_id="user_001",
                node_type="EVENT",
                display_label="爱人生日",
                sensitivity_level="SENSITIVE",
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
            LifeGraphNodeRecord(
                node_id="goal_gift_001",
                owner_user_id="user_001",
                node_type="GOAL",
                display_label="生日礼物心愿金",
                sensitivity_level="PERSONAL",
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
        ])

    with Session(engine) as database, database.begin():
        database.add_all([
            LifeGraphEdgeRecord(
                edge_id="edge_spouse_001",
                owner_user_id="user_001",
                source_node_id="person_self_001",
                target_node_id="person_spouse_001",
                relation_type="SPOUSE_OF",
                consent_scope={"allowed_uses": ["GOAL_PLANNING"]},
                provenance="USER_DECLARED",
                confidence_bp=10000,
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
            LifeGraphEdgeRecord(
                edge_id="edge_spouse_birthday_001",
                owner_user_id="user_001",
                source_node_id="person_spouse_001",
                target_node_id="event_birthday_001",
                relation_type="HAS_EVENT",
                consent_scope={"allowed_uses": ["REMINDER", "GOAL_PLANNING"]},
                provenance="USER_DECLARED",
                confidence_bp=10000,
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
            LifeGraphEdgeRecord(
                edge_id="edge_goal_event_001",
                owner_user_id="user_001",
                source_node_id="goal_gift_001",
                target_node_id="event_birthday_001",
                relation_type="TRIGGERED_BY",
                consent_scope={"allowed_uses": ["GOAL_PLANNING"]},
                provenance="USER_DECLARED",
                confidence_bp=10000,
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
            LifeEventRecord(
                event_node_id="event_birthday_001",
                owner_user_id="user_001",
                event_type="BIRTHDAY",
                occurs_at=birthday,
                timezone="Asia/Shanghai",
                recurrence_rule="FREQ=YEARLY",
                status="ACTIVE",
                details={"remind_days_before": 2},
                created_at=now,
                updated_at=now,
            ),
            FinancialGoalRecord(
                goal_node_id="goal_gift_001",
                owner_user_id="user_001",
                target_amount=Decimal("1000.00"),
                reserved_amount=Decimal("0.00"),
                currency="CNY",
                due_at=birthday,
                funding_policy={"mode": "RESERVE_AFTER_CONFIRMATION"},
                status="DRAFT",
                created_at=now,
                updated_at=now,
            ),
        ])

    with Session(engine) as database, database.begin():
        database.add(EventActionRecord(
            action_id="action_remind_001",
            owner_user_id="user_001",
            event_node_id="event_birthday_001",
            goal_node_id="goal_gift_001",
            action_type="REMIND_PURCHASE_PLAN",
            scheduled_at=reminder_at,
            status="PENDING",
            idempotency_key="birthday-2026-reminder",
            payload={"suggestions": ["鲜花", "蛋糕"]},
            created_at=now,
            updated_at=now,
        ))

    with Session(engine) as database:
        edges = database.scalars(
            select(LifeGraphEdgeRecord).where(
                LifeGraphEdgeRecord.owner_user_id == "user_001"
            )
        ).all()
        goal = database.get(FinancialGoalRecord, "goal_gift_001")
        action = database.get(EventActionRecord, "action_remind_001")

    assert {edge.relation_type for edge in edges} == {
        "SPOUSE_OF",
        "HAS_EVENT",
        "TRIGGERED_BY",
    }
    assert goal.target_amount == Decimal("1000.00")
    assert action.scheduled_at == reminder_at


def test_database_rejects_cross_user_graph_edge():
    engine = build_engine()
    now = datetime(2026, 10, 28, 10, 0, 0)

    with Session(engine) as database, database.begin():
        seed_users(database, now)
        database.flush()
        database.add_all([
            LifeGraphNodeRecord(
                node_id="person_user_1",
                owner_user_id="user_001",
                node_type="PERSON",
                display_label="用户一",
                sensitivity_level="PERSONAL",
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
            LifeGraphNodeRecord(
                node_id="person_user_2",
                owner_user_id="user_002",
                node_type="PERSON",
                display_label="用户二",
                sensitivity_level="PERSONAL",
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ),
        ])

    with pytest.raises(IntegrityError):
        with Session(engine) as database, database.begin():
            database.add(LifeGraphEdgeRecord(
                edge_id="edge_cross_user",
                owner_user_id="user_001",
                source_node_id="person_user_1",
                target_node_id="person_user_2",
                relation_type="SPOUSE_OF",
                consent_scope={},
                provenance="USER_DECLARED",
                confidence_bp=10000,
                status="ACTIVE",
                attributes={},
                created_at=now,
                updated_at=now,
            ))
