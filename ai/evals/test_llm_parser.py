import json
from types import SimpleNamespace

from ai.agent import llm_parser


class FakeCompletions:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error

    def create(self, **_: object):
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            model="deepseek-chat-2026-test",
            _request_id="request_test_001",
        )


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def configure(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")


def test_deepseek_output_is_schema_validated(monkeypatch):
    configure(monkeypatch)
    content = json.dumps({
        "name": "transfer.create",
        "confidence": 0.92,
        "slots": {"recipient": "小王", "amount": 200, "currency": "CNY"},
        "missing_slots": [],
        "risk_hints": [],
    }, ensure_ascii=False)
    monkeypatch.setattr(llm_parser, "_create_client", lambda *_: FakeClient(FakeCompletions(content)))

    intent, trace = llm_parser.parse_with_llm("给小王两百元", [])

    assert intent["slots"]["amount"] == 200
    assert intent["parser"] == "llm"
    assert trace["status"] == "SUCCEEDED"
    assert trace["response_model"] == "deepseek-chat-2026-test"


def test_invalid_deepseek_output_falls_back(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(llm_parser, "_create_client", lambda *_: FakeClient(FakeCompletions('{"name":"transfer.create"}')))

    intent, trace = llm_parser.parse_with_llm("给小王转账", [])

    assert intent is None
    assert trace["status"] == "INVALID_OUTPUT"
    assert trace["fallback_used"] is True


def test_timeout_is_recorded_without_exposing_secret(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(llm_parser, "_create_client", lambda *_: FakeClient(FakeCompletions(error=TimeoutError())))

    intent, trace = llm_parser.parse_with_llm("分析账单", [])

    assert intent is None
    assert trace["status"] == "TIMEOUT"
    assert "test-key-not-real" not in str(trace)


def test_client_dependency_error_falls_back(monkeypatch):
    configure(monkeypatch)

    def raise_import_error(*_: object):
        raise ImportError("optional proxy dependency is unavailable")

    monkeypatch.setattr(llm_parser, "_create_client", raise_import_error)

    intent, trace = llm_parser.parse_with_llm("分析账单", [])

    assert intent is None
    assert trace["status"] == "API_ERROR"
    assert trace["fallback_used"] is True
    assert trace["error_type"] == "ImportError"


def test_deepseek_life_event_output_is_schema_validated(monkeypatch):
    configure(monkeypatch)
    content = json.dumps({
        "name": "life.event_plan",
        "confidence": 0.95,
        "slots": {
            "related_person": "爱人",
            "relation_type": "spouse",
            "event_type": "birthday",
            "event_label": "爱人生日",
            "event_date": "2026-11-18",
            "event_year": 2026,
            "event_month": 11,
            "recurrence_rule": "FREQ=YEARLY",
            "goal_required": True,
            "goal_label": "爱人生日礼物心愿金",
            "target_amount": 1000,
            "currency": "CNY",
            "reminder_days_before": 2,
        },
        "missing_slots": [],
        "risk_hints": [],
    }, ensure_ascii=False)
    monkeypatch.setattr(llm_parser, "_create_client", lambda *_: FakeClient(FakeCompletions(content)))

    intent, trace = llm_parser.parse_with_llm("我爱人生日，准备一千元", [])

    assert intent["name"] == "life.event_plan"
    assert intent["slots"]["relation_type"] == "SPOUSE_OF"
    assert intent["slots"]["event_type"] == "BIRTHDAY"
    assert intent["slots"]["event_date"] == "2026-11-18"
    assert intent["slots"]["target_amount"] == 1000
    assert trace["status"] == "SUCCEEDED"
