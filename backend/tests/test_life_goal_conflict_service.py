from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.db.models import AccountRecord, Base, TransactionRecord, UserRecord
from backend.app.repositories.life_graph import LifeGraphRepository
from backend.app.services.life_goal_conflict_service import LifeGoalConflictService
from backend.app.services.life_graph_proposal import build_life_graph_proposal
from backend.app.services.life_graph_service import LifeGraphService


AS_OF = datetime(2026, 10, 29, 10, 0, 0)


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def seed_financial_evidence(engine, *, available_balance: str = "10000.00") -> None:
    with Session(engine) as database, database.begin():
        database.add(UserRecord(
            id="user_001",
            display_name="林安",
            phone_masked="138****1024",
            role="CUSTOMER",
            status="ACTIVE",
            risk_level="LOW",
            created_at=AS_OF,
            updated_at=AS_OF,
        ))
        database.flush()
        database.add(AccountRecord(
            id="acct_001",
            user_id="user_001",
            payload={
                "id": "acct_001",
                "user_id": "user_001",
                "currency": "CNY",
                "available_balance": available_balance,
            },
        ))
        database.add_all([
            TransactionRecord(
                id="txn_income",
                user_id="user_001",
                sort_order=0,
                payload={
                    "id": "txn_income",
                    "user_id": "user_001",
                    "amount": 8000,
                    "occurred_at": "2026-10-20T09:00:00",
                },
            ),
            TransactionRecord(
                id="txn_expense",
                user_id="user_001",
                sort_order=1,
                payload={
                    "id": "txn_expense",
                    "user_id": "user_001",
                    "amount": -3000,
                    "occurred_at": "2026-10-22T09:00:00",
                },
            ),
        ])


def birthday_slots() -> dict:
    return {
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
    }


def tuition_slots() -> dict:
    return {
        "related_person": "女儿",
        "relation_type": "PARENT_OF",
        "event_type": "TUITION_DUE",
        "event_label": "女儿学费缴纳",
        "event_date": "2026-11-15",
        "recurrence_rule": None,
        "goal_required": True,
        "goal_label": "女儿学费目标",
        "target_amount": 5000,
        "currency": "CNY",
        "reminder_days_before": None,
    }


def create_request(
    graph_service: LifeGraphService,
    graph_repository: LifeGraphRepository,
    *,
    request_key: str,
    slots: dict,
) -> dict:
    change_set, summary = build_life_graph_proposal(
        graph_repository,
        owner_user_id="user_001",
        slots=slots,
    )
    return graph_service.create_change_request(
        owner_user_id="user_001",
        idempotency_key=request_key,
        summary=summary,
        change_set=change_set,
        now=AS_OF,
    )["request"]


def setup_birthday_and_tuition_candidate():
    engine = build_engine()
    seed_financial_evidence(engine)
    repository = LifeGraphRepository(engine)
    graph_service = LifeGraphService(engine, repository)
    birthday = create_request(
        graph_service,
        repository,
        request_key="birthday",
        slots=birthday_slots(),
    )
    graph_service.decide_change_request(
        owner_user_id="user_001",
        request_id=birthday["request_id"],
        approved=True,
        now=AS_OF,
    )
    tuition = create_request(
        graph_service,
        repository,
        request_key="tuition",
        slots=tuition_slots(),
    )
    return engine, repository, tuition


def test_same_month_goals_report_explainable_cash_flow_pressure():
    engine, repository, tuition = setup_birthday_and_tuition_candidate()
    service = LifeGoalConflictService(engine, repository)

    result = service.analyze(
        owner_user_id="user_001",
        candidate_request_id=tuition["request_id"],
        as_of=AS_OF,
    )

    assert result["status"] == "PRESSURE"
    assert result["severity"] == "MEDIUM"
    assert result["evidence"]["available_balance"] == "10000.00"
    assert result["evidence"]["observed_30d_net_cash_flow"] == "5000.00"
    assert result["windows"][0]["period_goal_total"] == "6000.00"
    assert result["windows"][0]["liquidity_gap"] == "0.00"
    assert result["windows"][0]["cash_flow_gap"] == "1000.00"
    assert {goal["related_person"] for goal in result["windows"][0]["goals"]} == {
        "爱人",
        "女儿",
    }
    assert result["priority_decision"] == "USER_REQUIRED"
    assert all(not suggestion["auto_apply"] for suggestion in result["suggestions"])


def test_protected_balance_can_turn_pressure_into_a_hard_conflict():
    engine, repository, tuition = setup_birthday_and_tuition_candidate()
    service = LifeGoalConflictService(engine, repository)

    result = service.analyze(
        owner_user_id="user_001",
        candidate_request_id=tuition["request_id"],
        protected_balance=Decimal("5000.00"),
        as_of=AS_OF,
    )

    assert result["status"] == "CONFLICT"
    assert result["severity"] == "HIGH"
    assert result["evidence"]["spendable_balance"] == "5000.00"
    assert result["windows"][0]["liquidity_gap"] == "1000.00"


def test_single_goal_is_clear_and_no_adjustment_is_suggested():
    engine = build_engine()
    seed_financial_evidence(engine)
    repository = LifeGraphRepository(engine)
    graph_service = LifeGraphService(engine, repository)
    birthday = create_request(
        graph_service,
        repository,
        request_key="birthday-only",
        slots=birthday_slots(),
    )
    service = LifeGoalConflictService(engine, repository)

    result = service.analyze(
        owner_user_id="user_001",
        candidate_request_id=birthday["request_id"],
        as_of=AS_OF,
    )

    assert result["status"] == "CLEAR"
    assert result["windows"][0]["period_goal_total"] == "1000.00"
    assert result["suggestions"] == []


def test_missing_account_evidence_returns_unknown_instead_of_claiming_safety():
    engine = build_engine()
    with Session(engine) as database, database.begin():
        database.add(UserRecord(
            id="user_001",
            display_name="林安",
            phone_masked=None,
            role="CUSTOMER",
            status="ACTIVE",
            risk_level="LOW",
            created_at=AS_OF,
            updated_at=AS_OF,
        ))
    service = LifeGoalConflictService(engine, LifeGraphRepository(engine))

    result = service.analyze(owner_user_id="user_001", as_of=AS_OF)

    assert result["status"] == "UNKNOWN"
    assert "ACCOUNT_BALANCE_UNAVAILABLE" in result["explanation"]
