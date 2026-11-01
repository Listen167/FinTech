"""飞书 Webhook 验签、解密、时间窗与统一幂等链路测试。"""

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import Base, ChannelWorkerLeaseRecord, UserRecord
from backend.app.dependencies.feishu_webhook import get_feishu_webhook_handler
from backend.app.errors import ApplicationError
from backend.app.integrations.feishu_webhook import (
    FeishuWebhookHandler,
    FeishuWebhookVerifier,
)
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.repositories.memory import MemoryRepository
from backend.app.services.agent_runtime import AgentRuntime
from backend.app.services.channel_gateway import ChannelGatewayService
from backend.app.main import app


TOKEN = "verification-token-demo"
ENCRYPT_KEY = "encrypt-key-demo-32-characters-long"
PEPPER = "channel-webhook-pepper-32-characters"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class FakeReplier:
    def __init__(self):
        self.items = []

    def reply_text(self, **kwargs):
        self.items.append({"kind": "text", **kwargs})

    def reply_card(self, **kwargs):
        self.items.append({"kind": "card", **kwargs})


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
    with Session(engine) as database, database.begin():
        database.add(UserRecord(
            id="user_001",
            display_name="林安",
            phone_masked="138****1024",
            role="CUSTOMER",
            status="ACTIVE",
            risk_level="LOW",
            created_at=NOW.replace(tzinfo=None),
            updated_at=NOW.replace(tzinfo=None),
        ))
    return engine


def encrypt_payload(payload: dict) -> str:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    iv = b"0123456789abcdef"
    key = hashlib.sha256(ENCRYPT_KEY.encode("utf-8")).digest()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def signed_request(payload: dict, *, timestamp: datetime = NOW, signature: str | None = None):
    raw_body = json.dumps(
        {"encrypt": encrypt_payload(payload)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    timestamp_text = str(int(timestamp.timestamp()))
    nonce = "nonce-demo-001"
    expected = hashlib.sha256(
        timestamp_text.encode() + nonce.encode() + ENCRYPT_KEY.encode() + raw_body
    ).hexdigest()
    return raw_body, {
        "X-Lark-Request-Timestamp": timestamp_text,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature or expected,
    }


def event_payload(*, text: str, message_id: str) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt-{message_id}",
            "event_type": "im.message.receive_v1",
            "tenant_key": "tenant-demo",
            "token": TOKEN,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou-demo-001"},
                "sender_type": "user",
                "tenant_key": "tenant-demo",
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc-demo-001",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "create_time": str(int(NOW.timestamp() * 1000)),
            },
        },
    }


def build_handler():
    engine = build_engine()
    runtime = AgentRuntime(MemoryRepository())
    gateway = ChannelGatewayService(
        ChannelGatewayRepository(engine),
        runtime,
        identity_pepper=PEPPER,
    )
    replier = FakeReplier()
    handler = FeishuWebhookHandler(
        verifier=FeishuWebhookVerifier(
            verification_token=TOKEN,
            encrypt_key=ENCRYPT_KEY,
            max_skew_seconds=300,
        ),
        gateway=gateway,
        replier=replier,
        worker_lease_seconds=75,
        worker_id="webhook:test:worker-001",
    )
    return handler, gateway, runtime, replier, engine


def test_encrypted_url_verification_is_validated_before_challenge():
    verifier = FeishuWebhookVerifier(
        verification_token=TOKEN,
        encrypt_key=ENCRYPT_KEY,
    )
    raw_body, headers = signed_request({
        "type": "url_verification",
        "token": TOKEN,
        "challenge": "challenge-demo",
    })

    result = verifier.verify_and_decode(headers=headers, raw_body=raw_body, now=NOW)

    assert result["challenge"] == "challenge-demo"


def test_webhook_rejects_bad_signature_stale_request_and_wrong_token():
    verifier = FeishuWebhookVerifier(
        verification_token=TOKEN,
        encrypt_key=ENCRYPT_KEY,
        max_skew_seconds=300,
    )
    raw_body, headers = signed_request(event_payload(text="查询余额", message_id="om-bad"))
    with pytest.raises(ApplicationError, match="FEISHU_WEBHOOK_SIGNATURE_INVALID"):
        verifier.verify_and_decode(
            headers={**headers, "X-Lark-Signature": "0" * 64},
            raw_body=raw_body,
            now=NOW,
        )

    stale_body, stale_headers = signed_request(
        event_payload(text="查询余额", message_id="om-stale"),
        timestamp=NOW - timedelta(minutes=6),
    )
    with pytest.raises(ApplicationError, match="FEISHU_WEBHOOK_TIMESTAMP_INVALID"):
        verifier.verify_and_decode(headers=stale_headers, raw_body=stale_body, now=NOW)

    wrong_body, wrong_headers = signed_request({
        **event_payload(text="查询余额", message_id="om-token"),
        "header": {
            **event_payload(text="查询余额", message_id="om-token")["header"],
            "token": "wrong-token",
        },
    })
    with pytest.raises(ApplicationError, match="FEISHU_WEBHOOK_TOKEN_INVALID"):
        verifier.verify_and_decode(headers=wrong_headers, raw_body=wrong_body, now=NOW)


def test_webhook_and_gateway_share_replay_protection_and_reply_uuid(monkeypatch):
    monkeypatch.setattr(
        "backend.app.integrations.feishu_webhook.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: NOW)}),
    )
    handler, gateway, runtime, replier, engine = build_handler()
    token = gateway.create_link_token(user_id="user_001", channel="FEISHU")
    link_body, link_headers = signed_request(event_payload(
        text=f"绑定 {token['link_code']}",
        message_id="om-webhook-link",
    ))
    text_body, text_headers = signed_request(event_payload(
        text="帮我分析本月账单",
        message_id="om-webhook-text",
    ))

    assert handler.handle(headers=link_headers, raw_body=link_body)["code"] == 0
    first = handler.handle(headers=text_headers, raw_body=text_body)
    replay = handler.handle(headers=text_headers, raw_body=text_body)

    assert first["code"] == replay["code"] == 0
    assert len(runtime.repo.runs) == 1
    assert replier.items[-1]["idempotency_key"] == replier.items[-2]["idempotency_key"]
    with Session(engine) as database:
        worker = database.scalar(select(ChannelWorkerLeaseRecord))
        assert worker.transport == "WEBHOOK"
        assert worker.state == "ONLINE"


def test_webhook_api_accepts_verified_encrypted_challenge(monkeypatch):
    monkeypatch.setattr(
        "backend.app.integrations.feishu_webhook.datetime",
        type("FixedDateTime", (), {"now": staticmethod(lambda _tz: NOW)}),
    )
    handler, _, _, _, _ = build_handler()
    app.dependency_overrides[get_feishu_webhook_handler] = lambda: handler
    raw_body, headers = signed_request({
        "type": "url_verification",
        "token": TOKEN,
        "challenge": "challenge-through-api",
    })
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/channels/webhooks/feishu",
                content=raw_body,
                headers={**headers, "Content-Type": "application/json"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-through-api"}
