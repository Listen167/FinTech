from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import (
    AccountRecord,
    Base,
    FinancialGoalRecord,
    LifeGraphNodeRecord,
    UserRecord,
)
from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.family_funding import get_family_funding_service
from backend.app.main import app
from backend.app.schemas.family_funding import (
    CreateFundingConsentScopeRequest,
    EvidenceSource,
    FundingPlanningRequest,
    GlobalFundingConstraints,
    GoalConstraintInput,
    GoalRigidity,
    IncomeEvidence,
)
from backend.app.services.auth_service import AuthContext
from backend.app.services.family_funding_negotiation import (
    FamilyFundingError,
    FamilyFundingNegotiationService,
)
from backend.app.services.family_funding_workflow import FamilyFundingWorkflow
from backend.app.services.funding_constraint_extractor import FundingConstraintExtractor


NOW = datetime(2026, 11, 1, 9, 0, 0)


def build_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def seed(engine):
    with Session(engine) as database, database.begin():
        database.add_all([
            UserRecord(
                id="user_001", display_name="林安", phone_masked="138****1024",
                role="CUSTOMER", status="ACTIVE", risk_level="LOW",
                created_at=NOW, updated_at=NOW,
            ),
            UserRecord(
                id="user_002", display_name="周宁", phone_masked="139****2048",
                role="CUSTOMER", status="ACTIVE", risk_level="LOW",
                created_at=NOW, updated_at=NOW,
            ),
        ])
        database.flush()
        database.add(AccountRecord(
            id="acct_001",
            user_id="user_001",
            payload={"id": "acct_001", "currency": "CNY", "available_balance": 10000},
        ))
        database.add_all([
            LifeGraphNodeRecord(
                node_id="goal_tuition", owner_user_id="user_001", node_type="GOAL",
                display_label="女儿学费", sensitivity_level="PERSONAL", status="ACTIVE",
                attributes={}, created_at=NOW, updated_at=NOW,
            ),
            LifeGraphNodeRecord(
                node_id="goal_gift", owner_user_id="user_001", node_type="GOAL",
                display_label="妻子生日礼物", sensitivity_level="PERSONAL", status="ACTIVE",
                attributes={}, created_at=NOW, updated_at=NOW,
            ),
        ])
        database.flush()
        database.add_all([
            FinancialGoalRecord(
                goal_node_id="goal_tuition", owner_user_id="user_001",
                target_amount=Decimal("8000"), reserved_amount=Decimal("0"), currency="CNY",
                due_at=datetime(2026, 11, 20), funding_policy={"rigidity": "RIGID"},
                status="DRAFT", created_at=NOW, updated_at=NOW,
            ),
            FinancialGoalRecord(
                goal_node_id="goal_gift", owner_user_id="user_001",
                target_amount=Decimal("3000"), reserved_amount=Decimal("0"), currency="CNY",
                due_at=datetime(2026, 11, 30), funding_policy={"rigidity": "FLEXIBLE"},
                status="DRAFT", created_at=NOW, updated_at=NOW,
            ),
        ])


def planning():
    return FundingPlanningRequest(
        goals=[
            GoalConstraintInput(
                goal_id="goal_tuition", label="女儿学费", target_amount="8000",
                minimum_amount="8000", due_date=date(2026, 11, 20),
                rigidity=GoalRigidity.RIGID, relationship_context="女儿",
            ),
            GoalConstraintInput(
                goal_id="goal_gift", label="妻子生日礼物", target_amount="3000",
                minimum_amount="1000", due_date=date(2026, 11, 30),
                rigidity=GoalRigidity.FLEXIBLE, can_reduce=True, can_delay=True,
                can_installments=True, relationship_context="妻子",
            ),
        ],
        constraints=GlobalFundingConstraints(
            starting_balance="999999",
            protected_balance="0",
            allowed_account_ids=["acct_001"],
            planning_start=date(2026, 11, 1),
            planning_end=date(2027, 3, 31),
        ),
    )


def create_negotiation(service, key="funding-test-001"):
    return service.create(
        owner_user_id="user_001",
        idempotency_key=key,
        request_text="存款一万元，先保障八千元学费，再协商三千元礼物",
        planning=planning(),
        now=NOW,
    )["negotiation"]


def reduction_plan(record):
    return next(item for item in record["plans"] if item["plan_type"] == "REDUCE_FLEXIBLE")


def auth_context(user_id="user_001"):
    return AuthContext(
        user_id=user_id, session_id=f"session_{user_id}", token_id=f"token_{user_id}",
        credential_version=1, display_name="演示用户", phone_masked="138****0000",
        role="CUSTOMER", risk_level="LOW",
    )


def test_candidate_does_not_write_then_confirm_only_updates_goal_and_undo_restores():
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    created = create_negotiation(service)
    selected_plan = reduction_plan(created)

    selected = service.select_plan(
        owner_user_id="user_001", negotiation_id=created["negotiation_id"],
        plan_id=selected_plan["plan_id"], reason="学费刚性，礼物预算可缩减", now=NOW,
    )
    with Session(engine) as database:
        gift = database.get(FinancialGoalRecord, "goal_gift")
        account = database.get(AccountRecord, "acct_001")
        assert gift.target_amount == Decimal("3000")
        assert Decimal(str(account.payload["available_balance"])) == Decimal("10000")
    assert selected["status"] == "PENDING_CONFIRMATION"

    applied = service.confirm(
        owner_user_id="user_001", negotiation_id=created["negotiation_id"],
        reason="确认缩减礼物目标", now=NOW + timedelta(seconds=1),
    )
    with Session(engine) as database:
        gift = database.get(FinancialGoalRecord, "goal_gift")
        account = database.get(AccountRecord, "acct_001")
        assert gift.target_amount == Decimal("2000")
        assert Decimal(str(account.payload["available_balance"])) == Decimal("10000")
    assert applied["status"] == "APPLIED"
    assert applied["funds_execution_allowed"] is False

    undone = service.undo(
        owner_user_id="user_001", negotiation_id=created["negotiation_id"],
        reason="恢复原目标", now=NOW + timedelta(seconds=2),
    )
    with Session(engine) as database:
        assert database.get(FinancialGoalRecord, "goal_gift").target_amount == Decimal("3000")
    assert undone["status"] == "UNDONE"


def test_balance_change_invalidates_candidate_and_recalculates_before_confirmation():
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    created = create_negotiation(service, "funding-race-001")
    plan = reduction_plan(created)
    service.select_plan(
        owner_user_id="user_001", negotiation_id=created["negotiation_id"],
        plan_id=plan["plan_id"], reason="先选择", now=NOW,
    )
    with Session(engine) as database, database.begin():
        account = database.get(AccountRecord, "acct_001")
        account.payload = {**account.payload, "available_balance": 9000}

    with pytest.raises(FamilyFundingError, match="FUNDING_SNAPSHOT_CHANGED"):
        service.confirm(
            owner_user_id="user_001", negotiation_id=created["negotiation_id"],
            reason="确认旧方案", now=NOW + timedelta(seconds=1),
        )

    refreshed = service.get("user_001", created["negotiation_id"])
    assert refreshed["status"] == "WAITING_SELECTION"
    assert refreshed["selected_plan_id"] is None
    assert reduction_plan(refreshed)["adjustments"][0]["target_amount"] == "1000.00"
    assert service.audits("user_001", created["negotiation_id"])[-1]["event_type"] == "PLAN_INVALIDATED_AND_RECALCULATED"


def test_cross_user_read_is_forbidden_and_idempotency_content_is_checked():
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    created = create_negotiation(service, "funding-private-001")

    with pytest.raises(FamilyFundingError, match="RESOURCE_FORBIDDEN"):
        service.get("user_002", created["negotiation_id"])
    with pytest.raises(FamilyFundingError, match="FUNDING_IDEMPOTENCY_CONFLICT"):
        service.create(
            owner_user_id="user_001", idempotency_key="funding-private-001",
            request_text="不同的请求", planning=planning(), now=NOW,
        )


def test_consent_scope_is_limited_and_auto_apply_cannot_be_enabled():
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    payload = CreateFundingConsentScopeRequest(
        goal_node_ids=["goal_gift"], amount_lower_bound="1000", amount_upper_bound="3000",
        protected_balance="5000", max_single_adjustment="500",
        valid_from=NOW, valid_until=NOW + timedelta(days=30), auto_apply_enabled=False,
    )

    created = service.create_scope(owner_user_id="user_001", payload=payload, now=NOW)
    revoked = service.revoke_scope(
        owner_user_id="user_001", scope_id=created["scope_id"], now=NOW + timedelta(seconds=1)
    )

    assert created["auto_apply_enabled"] is False
    assert revoked["status"] == "REVOKED"


def test_expired_consent_scope_cannot_be_revoked_as_active():
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    payload = CreateFundingConsentScopeRequest(
        goal_node_ids=["goal_gift"], amount_lower_bound="1000", amount_upper_bound="3000",
        protected_balance="5000", max_single_adjustment="500",
        valid_from=NOW, valid_until=NOW + timedelta(seconds=1), auto_apply_enabled=False,
    )
    created = service.create_scope(owner_user_id="user_001", payload=payload, now=NOW)

    with pytest.raises(FamilyFundingError, match="FUNDING_CONSENT_SCOPE_EXPIRED"):
        service.revoke_scope(
            owner_user_id="user_001", scope_id=created["scope_id"],
            now=NOW + timedelta(seconds=2),
        )

    assert service.list_scopes("user_001")[0]["status"] == "EXPIRED"


def test_client_cannot_forge_bank_confirmed_income():
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    base = planning()
    forged_income = IncomeEvidence(
        income_id="forged", label="伪造银行入账", amount="5000",
        available_on=date(2026, 11, 10), source=EvidenceSource.BANK_CONFIRMED,
        confidence_bp=10000, reference_id="missing_transaction",
    )
    forged = base.model_copy(update={
        "constraints": base.constraints.model_copy(update={"incomes": [forged_income]})
    })

    with pytest.raises(FamilyFundingError, match="FUNDING_BANK_INCOME_UNVERIFIED"):
        service.create(
            owner_user_id="user_001", idempotency_key="funding-forged-income",
            request_text="伪造银行确认收入", planning=forged, now=NOW,
        )


def test_workflow_trace_covers_required_negotiation_nodes():
    state = FamilyFundingWorkflow().invoke({
        "event": "CREATE", "planning": planning().model_dump(mode="json"),
        "missing_fields": [], "trace": [],
    })

    assert state["stage"] == "WAITING_SELECTION"
    assert state["trace"] == [
        "load_context", "complete_constraints", "build_timeline",
        "calculate_plans", "await_selection",
    ]


def test_invalid_deepseek_output_falls_back_to_waiting_input(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    response = SimpleNamespace(
        model="deepseek-chat",
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"goals": []}'))],
        _request_id="request-test",
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: response)
        )
    )
    extractor = FundingConstraintExtractor(client_factory=lambda *_: client)

    result, missing, trace = extractor.extract(request_text="帮我规划", trusted_context={})

    assert result is None
    assert missing
    assert trace["status"] == "INVALID_OUTPUT"
    assert trace["error_code"] == "SCHEMA_VALIDATION_FAILED"


def test_waiting_input_can_be_completed_and_invalid_model_plan_is_audited(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    waiting = service.create(
        owner_user_id="user_001", idempotency_key="funding-input-001",
        request_text="请帮我协调两个目标", planning=None, now=NOW,
    )["negotiation"]

    completed = service.complete_constraints(
        owner_user_id="user_001", negotiation_id=waiting["negotiation_id"],
        planning=planning(), reason="补充刚柔性、最低金额和截止日期", now=NOW,
    )
    from backend.app.schemas.family_funding import GoalAdjustment

    with pytest.raises(FamilyFundingError, match="FUNDING_MODEL_PLAN_REJECTED"):
        service.add_custom_plan(
            owner_user_id="user_001", negotiation_id=waiting["negotiation_id"],
            title="非法缩减", model_explanation="尝试突破最低金额",
            adjustments=[GoalAdjustment(
                goal_id="goal_gift", target_amount="500", reason="模型建议"
            )], now=NOW + timedelta(seconds=1),
        )

    assert waiting["status"] == "WAITING_INPUT"
    assert completed["status"] == "WAITING_SELECTION"
    assert service.audits("user_001", waiting["negotiation_id"])[-1]["event_type"] == "MODEL_PLAN_REJECTED"


@pytest.fixture
def api_setup():
    engine = build_engine()
    seed(engine)
    service = FamilyFundingNegotiationService(engine)
    app.dependency_overrides[get_family_funding_service] = lambda: service
    app.dependency_overrides[get_current_auth] = lambda: auth_context()
    try:
        with TestClient(app) as client:
            yield client, service
    finally:
        app.dependency_overrides.clear()


def test_family_funding_http_flow_and_stable_error(api_setup):
    client, _ = api_setup
    payload = {
        "request_text": "协调学费和生日礼物",
        "planning": planning().model_dump(mode="json"),
    }
    created = client.post(
        "/api/family-funding/negotiations",
        json=payload,
        headers={"Idempotency-Key": "api-funding-001"},
    )
    body = created.json()["negotiation"]
    infeasible = next(item for item in body["plans"] if item["feasibility"] == "INFEASIBLE")
    rejected = client.post(
        f"/api/family-funding/negotiations/{body['negotiation_id']}/select",
        json={"plan_id": infeasible["plan_id"], "reason": "测试不可行方案"},
    )

    assert created.status_code == 201
    assert body["constraints"]["constraints"]["starting_balance"] == "10000.00"
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "FUNDING_PLAN_INFEASIBLE"
