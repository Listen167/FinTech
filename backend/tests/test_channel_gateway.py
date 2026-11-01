"""Channel Gateway 身份绑定、幂等和安全边界测试。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.api.v1.channels import router as channel_router
from backend.app.core.config import settings
from backend.app.db.models import (
    Base,
    ChannelConversationRecord,
    ChannelIdentityRecord,
    ChannelLinkTokenRecord,
    ChannelMessageRecord,
    ChannelWorkerLeaseRecord,
    UserRecord,
)
from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.channel_gateway import (
    get_channel_gateway_service,
    require_channel_gateway_key,
)
from backend.app.errors import ApplicationError
from backend.app.main import app
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.repositories.memory import MemoryRepository
from backend.app.schemas.channel import ChannelWorkerHeartbeat, InboundChannelMessage
from backend.app.services.agent_runtime import AgentRuntime
from backend.app.services.auth_service import AuthContext
from backend.app.services.channel_gateway import ChannelGatewayService


PEPPER = "channel-test-pepper-32-characters-long"
GATEWAY_KEY = "channel-test-gateway-key-32-characters"


def build_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(database_connection, _):
        database_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def seed_users(engine) -> None:
    now = datetime(2026, 10, 30, 9, 0, 0)
    with Session(engine) as database, database.begin():
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


def auth_context(user_id: str = "user_001") -> AuthContext:
    return AuthContext(
        user_id=user_id,
        session_id=f"auth_{user_id}",
        token_id=f"token_{user_id}",
        credential_version=1,
        display_name="演示用户",
        phone_masked="138****0000",
        role="CUSTOMER",
        risk_level="LOW",
    )


def inbound(
    *,
    message_id: str,
    kind: str = "TEXT",
    text: str | None = "帮我分析本月账单",
    link_code: str | None = None,
    external_user_id: str = "ou_demo_001",
    conversation_id: str = "oc_demo_001",
) -> InboundChannelMessage:
    return InboundChannelMessage(
        channel="FEISHU",
        tenant_id="tenant_demo",
        external_message_id=message_id,
        external_conversation_id=conversation_id,
        external_user_id=external_user_id,
        kind=kind,
        text=text if kind == "TEXT" else None,
        link_code=link_code if kind == "LINK" else None,
        occurred_at=datetime(2026, 10, 30, 9, 30, tzinfo=timezone.utc),
    )


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")
    engine = build_engine()
    seed_users(engine)
    agent_runtime = AgentRuntime(MemoryRepository())
    service = ChannelGatewayService(
        ChannelGatewayRepository(engine),
        agent_runtime,
        identity_pepper=PEPPER,
        link_token_ttl_seconds=600,
    )
    return service, engine, agent_runtime


def bind_identity(
    service: ChannelGatewayService,
    *,
    user_id: str = "user_001",
    external_user_id: str = "ou_demo_001",
    message_id: str = "om_link_001",
) -> dict:
    token = service.create_link_token(user_id=user_id, channel="FEISHU")
    return service.process_inbound(inbound(
        message_id=message_id,
        kind="LINK",
        text=None,
        link_code=token["link_code"],
        external_user_id=external_user_id,
    ))


def test_link_token_binds_identity_without_storing_raw_external_ids(gateway):
    service, engine, _ = gateway
    linked = bind_identity(service)
    identities = service.list_identities(user_id="user_001")

    assert linked["status"] == "PROCESSED"
    assert linked["binding"]["status"] == "ACTIVE"
    assert identities[0]["external_user_fingerprint"] != "ou_demo_001"
    assert identities[0]["external_user_fingerprint"][6:10] == "****"
    assert identities[0]["last_active_at"] is not None
    assert "user_id" not in identities[0]
    assert "tenant_id" not in identities[0]
    with Session(engine) as database:
        token = database.scalar(select(ChannelLinkTokenRecord))
        identity = database.scalar(select(ChannelIdentityRecord))
        assert token.status == "USED"
        assert token.token_hash != linked.get("link_code")
        assert identity.external_user_id_hash != "ou_demo_001"
        assert identity.external_user_id_ciphertext != "ou_demo_001"
        assert service.identity_cipher.decrypt(identity.external_user_id_ciphertext) == "ou_demo_001"
        assert "external_user_id_ciphertext" not in identities[0]


def test_new_link_code_cancels_previous_unused_code(gateway):
    service, engine, _ = gateway
    first = service.create_link_token(user_id="user_001", channel="FEISHU")
    second = service.create_link_token(user_id="user_001", channel="FEISHU")

    rejected = service.process_inbound(inbound(
        message_id="om_old_link_code",
        kind="LINK",
        text=None,
        link_code=first["link_code"],
    ))
    accepted = service.process_inbound(inbound(
        message_id="om_new_link_code",
        kind="LINK",
        text=None,
        link_code=second["link_code"],
    ))

    assert rejected["error"]["code"] == "CHANNEL_LINK_TOKEN_INVALID"
    assert accepted["status"] == "PROCESSED"
    with Session(engine) as database:
        statuses = database.scalars(
            select(ChannelLinkTokenRecord.status).order_by(ChannelLinkTokenRecord.created_at)
        ).all()
        assert statuses == ["CANCELED", "USED"]


def test_unbound_message_is_rejected_without_creating_agent_run(gateway):
    service, _, agent_runtime = gateway
    response = service.process_inbound(inbound(message_id="om_unbound_001"))

    assert response["status"] == "REJECTED"
    assert response["error"]["code"] == "CHANNEL_BINDING_REQUIRED"
    assert agent_runtime.repo.runs == {}


def test_duplicate_message_returns_same_run_without_reexecution(gateway):
    service, _, agent_runtime = gateway
    bind_identity(service)
    payload = inbound(message_id="om_bill_001")

    first = service.process_inbound(payload)
    replay = service.process_inbound(payload)

    assert first["agent"]["state"] == "SUCCEEDED"
    assert replay["replayed"] is True
    assert replay["agent"]["run_id"] == first["agent"]["run_id"]
    assert len(agent_runtime.repo.runs) == 1


def test_channel_conversation_recovers_after_agent_demo_reset(gateway):
    service, engine, agent_runtime = gateway
    bind_identity(service)

    first = service.process_inbound(inbound(message_id="om_before_reset"))
    old_session_id = first["agent"]["session_id"]
    agent_runtime.reset_demo()

    recovered = service.process_inbound(inbound(
        message_id="om_after_reset",
        text="帮我分析本月账单",
    ))

    assert recovered["status"] == "PROCESSED"
    assert recovered["agent"]["state"] == "SUCCEEDED"
    assert recovered["agent"]["session_id"] != old_session_id
    assert len(agent_runtime.repo.runs) == 1
    with Session(engine) as database:
        identity = database.scalar(select(ChannelIdentityRecord))
        conversation = database.scalar(select(ChannelConversationRecord))
        assert identity.status == "ACTIVE"
        assert conversation.agent_session_id == recovered["agent"]["session_id"]

    replay = service.process_inbound(inbound(
        message_id="om_after_reset",
        text="帮我分析本月账单",
    ))
    assert replay["replayed"] is True
    assert replay["agent"]["run_id"] == recovered["agent"]["run_id"]
    assert len(agent_runtime.repo.runs) == 1


def test_channel_conversation_does_not_rebind_on_user_mismatch(gateway):
    service, engine, agent_runtime = gateway
    bind_identity(service)
    service.process_inbound(inbound(message_id="om_owner_session"))
    foreign_session = agent_runtime.create_session(user_id="user_002", channel="FEISHU")

    with Session(engine) as database, database.begin():
        conversation = database.scalar(select(ChannelConversationRecord))
        conversation.agent_session_id = foreign_session["session_id"]

    with pytest.raises(ApplicationError, match="CHANNEL_PROCESSING_FAILED"):
        service.process_inbound(inbound(message_id="om_user_mismatch"))

    with Session(engine) as database:
        conversation = database.scalar(select(ChannelConversationRecord))
        assert conversation.agent_session_id == foreign_session["session_id"]
    assert len(agent_runtime.repo.sessions) == 2


def test_channel_follow_up_explains_previous_bill_result(gateway):
    service, _, agent_runtime = gateway
    bind_identity(service)

    first = service.process_inbound(inbound(message_id="om_bill_detail_001"))
    follow_up = service.process_inbound(inbound(
        message_id="om_bill_detail_002",
        text="哪一笔，为什么异常？",
    ))

    assert follow_up["agent"]["session_id"] == first["agent"]["session_id"]
    assert "陌生数码商户" in follow_up["reply"]["text"]
    assert "原因：" in follow_up["reply"]["text"]
    assert len(agent_runtime.repo.runs) == 2


def test_same_message_id_with_changed_payload_is_rejected(gateway):
    service, _, _ = gateway
    bind_identity(service)
    service.process_inbound(inbound(message_id="om_conflict_001"))

    with pytest.raises(ApplicationError, match="CHANNEL_MESSAGE_CONFLICT"):
        service.process_inbound(inbound(
            message_id="om_conflict_001",
            text="给小王转 200 元",
        ))


def test_red_operation_requires_trusted_app_verification(gateway):
    service, _, agent_runtime = gateway
    bind_identity(service)

    response = service.process_inbound(inbound(
        message_id="om_red_001",
        text="转给小王 20000 元",
    ))
    run = agent_runtime.get_run(response["agent"]["run_id"], user_id="user_001")

    assert run["state"] == "WAITING_VERIFICATION"
    assert response["security"]["mode"] == "APP_REQUIRED"
    assert response["security"]["action"] == "OPEN_APP_VERIFICATION"
    assert response["agent"]["pending_action"] == "VERIFICATION"
    deep_link = urlsplit(response["security"]["deep_link"])
    query = parse_qs(deep_link.query)
    assert f"{deep_link.scheme}://{deep_link.netloc}" == "http://127.0.0.1:5173"
    assert query == {
        "secure_action": ["verification"],
        "run_id": [run["run_id"]],
    }
    assert "token" not in response["security"]["deep_link"].lower()


def test_link_code_replay_and_cross_user_identity_claim_are_rejected(gateway):
    service, _, _ = gateway
    first_token = service.create_link_token(user_id="user_001", channel="FEISHU")
    first = service.process_inbound(inbound(
        message_id="om_link_first",
        kind="LINK",
        text=None,
        link_code=first_token["link_code"],
    ))
    replay = service.process_inbound(inbound(
        message_id="om_link_replay",
        kind="LINK",
        text=None,
        link_code=first_token["link_code"],
    ))
    second_token = service.create_link_token(user_id="user_002", channel="FEISHU")
    claimed = service.process_inbound(inbound(
        message_id="om_link_claim",
        kind="LINK",
        text=None,
        link_code=second_token["link_code"],
    ))

    assert first["status"] == "PROCESSED"
    assert replay["error"]["code"] == "CHANNEL_LINK_TOKEN_REPLAYED"
    assert claimed["error"]["code"] == "CHANNEL_IDENTITY_ALREADY_BOUND"


def test_revoked_identity_cannot_continue_and_is_user_scoped(gateway):
    service, _, _ = gateway
    linked = bind_identity(service)
    identity_id = linked["binding"]["identity_id"]

    with pytest.raises(ApplicationError, match="RESOURCE_FORBIDDEN"):
        service.revoke_identity(user_id="user_002", identity_id=identity_id)
    service.revoke_identity(user_id="user_001", identity_id=identity_id)
    response = service.process_inbound(inbound(message_id="om_after_revoke"))

    assert response["error"]["code"] == "CHANNEL_BINDING_REQUIRED"


def test_expired_link_token_is_rejected(gateway):
    service, engine, _ = gateway
    token = service.create_link_token(user_id="user_001", channel="FEISHU")
    with Session(engine) as database, database.begin():
        record = database.scalar(select(ChannelLinkTokenRecord))
        record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)

    response = service.process_inbound(inbound(
        message_id="om_expired_link",
        kind="LINK",
        text=None,
        link_code=token["link_code"],
    ))

    assert response["error"]["code"] == "CHANNEL_LINK_TOKEN_EXPIRED"
    with Session(engine) as database:
        assert database.scalar(select(ChannelLinkTokenRecord)).status == "EXPIRED"


def test_channel_api_requires_adapter_key_and_exposes_user_scoped_binding(gateway, monkeypatch):
    service, _, _ = gateway
    app.dependency_overrides[get_channel_gateway_service] = lambda: service
    app.dependency_overrides[get_current_auth] = lambda: auth_context("user_001")
    monkeypatch.setattr(settings, "channel_gateway_secret", SecretStr(GATEWAY_KEY))
    try:
        with TestClient(app) as client:
            token = client.post("/api/channels/link-tokens", json={"channel": "FEISHU"})
            unauthorized = client.post(
                "/api/channels/messages/inbound",
                json=inbound(message_id="om_api_unauthorized").model_dump(mode="json"),
            )
            heartbeat_unauthorized = client.post(
                "/api/channels/workers/heartbeat",
                json={
                    "worker_id": "feishu:api-test:worker-001",
                    "worker_kind": "FEISHU_INBOUND",
                    "transport": "LONG_CONNECTION",
                    "state": "ONLINE",
                },
            )
            heartbeat = client.post(
                "/api/channels/workers/heartbeat",
                headers={"X-Channel-Gateway-Key": GATEWAY_KEY},
                json={
                    "worker_id": "feishu:api-test:worker-001",
                    "worker_kind": "FEISHU_INBOUND",
                    "transport": "LONG_CONNECTION",
                    "state": "ONLINE",
                },
            )
            linked = client.post(
                "/api/channels/messages/inbound",
                headers={"X-Channel-Gateway-Key": GATEWAY_KEY},
                json=inbound(
                    message_id="om_api_link",
                    kind="LINK",
                    text=None,
                    link_code=token.json()["link_code"],
                ).model_dump(mode="json"),
            )
            identities = client.get("/api/channels/identities")
            management = client.get("/api/channels/status")
    finally:
        app.dependency_overrides.clear()

    assert token.status_code == 201
    assert unauthorized.status_code == 401
    assert unauthorized.json()["detail"]["code"] == "CHANNEL_GATEWAY_UNAUTHORIZED"
    assert heartbeat_unauthorized.status_code == 401
    assert heartbeat.status_code == 200
    assert heartbeat.json()["online"] is True
    assert linked.status_code == 200
    assert linked.json()["binding"]["status"] == "ACTIVE"
    assert identities.json()["total"] == 1
    assert management.status_code == 200
    assert management.json()["online"] is True


def test_channel_schema_rejects_ambiguous_payload():
    with pytest.raises(ValueError, match="TEXT 消息不能携带 link_code"):
        InboundChannelMessage(
            channel="FEISHU",
            tenant_id="tenant_demo",
            external_message_id="om_invalid",
            external_conversation_id="oc_demo",
            external_user_id="ou_demo",
            kind="TEXT",
            text="查询余额",
            link_code="ABCDEFGH",
            occurred_at=datetime.now(timezone.utc),
        )


def test_worker_heartbeat_distinguishes_configured_online_and_expired(gateway):
    service, engine, _ = gateway
    heartbeat = service.record_worker_heartbeat(
        ChannelWorkerHeartbeat(
            worker_id="feishu:test-host:worker-001",
            worker_kind="FEISHU_INBOUND",
            transport="LONG_CONNECTION",
            state="ONLINE",
            lease_seconds=60,
        ),
        default_lease_seconds=75,
    )
    online = service.management_status(
        gateway_configured=True,
        feishu_configured=True,
        webhook_configured=False,
    )

    assert heartbeat["online"] is True
    assert "test-host" not in heartbeat["worker_fingerprint"]
    assert online["online"] is True
    assert online["transports"][0]["configured"] is True
    assert online["transports"][0]["online"] is True

    with Session(engine) as database, database.begin():
        record = database.scalar(select(ChannelWorkerLeaseRecord))
        record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    expired = service.management_status(
        gateway_configured=True,
        feishu_configured=True,
        webhook_configured=False,
    )
    assert expired["online"] is False
    assert expired["workers"][0]["state"] == "OFFLINE"


def test_worker_id_cannot_change_kind_or_transport(gateway):
    service, _, _ = gateway
    service.record_worker_heartbeat(
        ChannelWorkerHeartbeat(
            worker_id="shared-worker-identity",
            worker_kind="FEISHU_INBOUND",
            transport="LONG_CONNECTION",
        ),
        default_lease_seconds=75,
    )
    with pytest.raises(ApplicationError, match="CHANNEL_WORKER_ID_CONFLICT"):
        service.record_worker_heartbeat(
            ChannelWorkerHeartbeat(
                worker_id="shared-worker-identity",
                worker_kind="LIFE_RADAR",
                transport="SCHEDULER",
            ),
            default_lease_seconds=75,
        )
