"""飞书长连接适配器的离线测试。"""

import json
from concurrent.futures import Future
from types import SimpleNamespace

import httpx
import pytest

from backend.app.errors import ApplicationError
from backend.app.integrations.feishu_adapter import (
    PRIVATE_CHAT_ONLY_REPLY,
    SECURE_APP_NOTICE,
    UNSUPPORTED_MESSAGE_REPLY,
    FeishuEventError,
    FeishuMessageProcessor,
    HttpChannelGatewayClient,
    build_gateway_card,
    format_gateway_reply,
    parse_feishu_event,
)
from backend.app.integrations.feishu_long_connection import FeishuReplyClient


def feishu_event(
    *,
    text: str = "帮我分析本月账单",
    message_id: str = "om_demo_001",
    message_type: str = "text",
    chat_type: str = "p2p",
    sender_type: str = "user",
    content: str | None = None,
):
    sender = SimpleNamespace(
        sender_type=sender_type,
        tenant_key="tenant_demo",
        sender_id=SimpleNamespace(
            open_id="ou_demo_001",
            user_id=None,
            union_id=None,
        ),
    )
    message = SimpleNamespace(
        message_id=message_id,
        chat_id="oc_demo_001",
        chat_type=chat_type,
        message_type=message_type,
        content=content if content is not None else json.dumps({"text": text}),
        create_time=1793323800000,
    )
    return SimpleNamespace(
        header=SimpleNamespace(tenant_key="tenant_demo"),
        event=SimpleNamespace(sender=sender, message=message),
    )


class ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as error:
            future.set_exception(error)
        return future


class FakeGateway:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response or {
            "gateway_message_id": "chmsg_demo_001",
            "reply": {"text": "账单分析已完成。"},
            "security": {"mode": "CHANNEL_ONLY", "action": "NONE"},
        }
        self.error = error
        self.received = []

    def process_inbound(self, payload):
        self.received.append(payload)
        if self.error:
            raise self.error
        return self.response


class FakeReplier:
    def __init__(self):
        self.items = []

    def reply_text(self, **kwargs):
        self.items.append({"kind": "text", **kwargs})

    def reply_card(self, **kwargs):
        self.items.append({"kind": "card", **kwargs})


def test_private_text_is_normalized_for_channel_gateway():
    parsed = parse_feishu_event(feishu_event())

    assert parsed.inbound.channel == "FEISHU"
    assert parsed.inbound.kind == "TEXT"
    assert parsed.inbound.text == "帮我分析本月账单"
    assert parsed.inbound.external_user_id == "ou_demo_001"
    assert parsed.inbound.external_conversation_id == "oc_demo_001"
    assert parsed.inbound.occurred_at.tzinfo is not None


@pytest.mark.parametrize("text", ["绑定 ABCD-EFGH", "bind abcd-efgh", "绑定：ABCDEFGH"])
def test_link_command_is_normalized_as_single_use_binding(text):
    parsed = parse_feishu_event(feishu_event(text=text))

    assert parsed.inbound.kind == "LINK"
    assert parsed.inbound.link_code == "ABCD-EFGH" or parsed.inbound.link_code == "ABCDEFGH"
    assert parsed.inbound.text is None


def test_group_and_non_text_messages_do_not_enter_banking_agent():
    group = parse_feishu_event(feishu_event(chat_type="group"))
    image = parse_feishu_event(feishu_event(message_type="image"))

    assert group.inbound is None
    assert group.immediate_reply == PRIVATE_CHAT_ONLY_REPLY
    assert image.inbound is None
    assert image.immediate_reply == UNSUPPORTED_MESSAGE_REPLY


def test_bot_message_is_ignored_to_prevent_reply_loop():
    parsed = parse_feishu_event(feishu_event(sender_type="bot"))

    assert parsed.ignored is True
    assert parsed.inbound is None


def test_invalid_text_payload_is_rejected_without_internal_details():
    with pytest.raises(FeishuEventError, match="FEISHU_TEXT_CONTENT_INVALID"):
        parse_feishu_event(feishu_event(content="not-json"))


def test_processor_enqueues_gateway_call_and_replies_with_stable_key():
    gateway = FakeGateway()
    replier = FakeReplier()
    processor = FeishuMessageProcessor(
        gateway,
        replier,
        executor=ImmediateExecutor(),
    )

    processor.handle_event(feishu_event())

    assert len(gateway.received) == 1
    assert replier.items[0]["kind"] == "text"
    assert replier.items[0]["message_id"] == "om_demo_001"
    assert replier.items[0]["text"] == "账单分析已完成。"
    assert replier.items[0]["idempotency_key"].startswith("sbg_")


def test_processor_returns_public_application_error_message():
    gateway = FakeGateway(error=ApplicationError("CHANNEL_MESSAGE_IN_PROGRESS"))
    replier = FakeReplier()
    processor = FeishuMessageProcessor(
        gateway,
        replier,
        executor=ImmediateExecutor(),
    )

    processor.handle_event(feishu_event())

    assert replier.items[0]["text"] == "该渠道消息正在处理中，请稍后查询。"
    assert "CHANNEL_MESSAGE_IN_PROGRESS" not in replier.items[0]["text"]


def test_secure_operation_reply_requires_return_to_trusted_app():
    response = {
        "reply": {"text": "大额转账需要强验证。"},
        "security": {
            "mode": "APP_REQUIRED",
            "action": "OPEN_APP_VERIFICATION",
            "risk_level": "RED",
            "action_label": "前往强验证",
            "deep_link": "http://127.0.0.1:5173/?secure_action=verification&run_id=run_demo",
        },
    }

    result = format_gateway_reply(response)
    card = build_gateway_card(response)

    assert result.startswith("大额转账需要强验证。")
    assert SECURE_APP_NOTICE in result
    assert card["header"]["template"] == "red"
    assert card["elements"][-1]["actions"][0]["url"].endswith("run_id=run_demo")
    assert card["elements"][-1]["actions"][0]["text"]["content"] == "前往强验证"


def test_processor_uses_interactive_card_for_secure_app_action():
    gateway = FakeGateway(response={
        "gateway_message_id": "chmsg_secure_001",
        "reply": {"text": "请核对收款人和金额。"},
        "security": {
            "mode": "APP_REQUIRED",
            "action": "OPEN_APP_CONFIRMATION",
            "risk_level": "YELLOW",
            "action_label": "前往安全确认",
            "deep_link": "http://127.0.0.1:5173/?secure_action=confirmation&run_id=run_demo",
        },
    })
    replier = FakeReplier()
    processor = FeishuMessageProcessor(gateway, replier, executor=ImmediateExecutor())

    processor.handle_event(feishu_event(text="给小王转 200 元"))

    assert replier.items[0]["kind"] == "card"
    assert replier.items[0]["card"]["header"]["template"] == "orange"
    assert replier.items[0]["idempotency_key"].startswith("sbg_")


def test_sdk_reply_client_builds_text_request_with_uuid():
    captured = []

    class ReplyResource:
        def reply(self, request):
            captured.append(request)
            return SimpleNamespace(success=lambda: True)

    sdk_client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=ReplyResource()))
    )
    replier = FeishuReplyClient(sdk_client)

    replier.reply_text(
        message_id="om_demo_001",
        text="测试回复",
        idempotency_key="sbg_demo_uuid",
    )

    assert captured[0].message_id == "om_demo_001"
    assert captured[0].request_body.msg_type == "text"
    assert captured[0].request_body.uuid == "sbg_demo_uuid"
    assert json.loads(captured[0].request_body.content) == {"text": "测试回复"}


def test_sdk_reply_client_builds_interactive_card_request():
    captured = []

    class ReplyResource:
        def reply(self, request):
            captured.append(request)
            return SimpleNamespace(success=lambda: True)

    sdk_client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=ReplyResource()))
    )
    replier = FeishuReplyClient(sdk_client)
    card = {"header": {"template": "red"}, "elements": []}

    replier.reply_card(
        message_id="om_demo_card",
        card=card,
        idempotency_key="sbg_demo_card_uuid",
    )

    assert captured[0].request_body.msg_type == "interactive"
    assert captured[0].request_body.uuid == "sbg_demo_card_uuid"
    assert json.loads(captured[0].request_body.content) == card


def test_http_gateway_client_uses_internal_key_and_returns_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Channel-Gateway-Key"] == "gateway-secret"
        assert request.url.path == "/api/channels/messages/inbound"
        return httpx.Response(
            200,
            json={
                "gateway_message_id": "chmsg_http_001",
                "reply": {"text": "处理成功"},
                "security": {"mode": "CHANNEL_ONLY", "action": "NONE"},
            },
        )

    client = HttpChannelGatewayClient(
        base_url="http://127.0.0.1:8000",
        gateway_secret="gateway-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.process_inbound(parse_feishu_event(feishu_event()).inbound)
    finally:
        client.close()

    assert result["gateway_message_id"] == "chmsg_http_001"


def test_http_gateway_client_translates_public_error_code():
    transport = httpx.MockTransport(lambda _: httpx.Response(
        409,
        json={
            "detail": {
                "code": "CHANNEL_MESSAGE_IN_PROGRESS",
                "message": "处理中",
                "retryable": True,
            }
        },
    ))
    client = HttpChannelGatewayClient(
        base_url="http://127.0.0.1:8000",
        gateway_secret="gateway-secret",
        transport=transport,
    )
    try:
        with pytest.raises(ApplicationError, match="CHANNEL_MESSAGE_IN_PROGRESS"):
            client.process_inbound(parse_feishu_event(feishu_event()).inbound)
    finally:
        client.close()
