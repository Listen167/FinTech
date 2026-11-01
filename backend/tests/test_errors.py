"""统一应用错误契约测试。"""

import pytest

from backend.app.errors import ApplicationError, error_definition


@pytest.mark.parametrize(
    ("code", "status_code", "retryable"),
    [
        ("SESSION_NOT_FOUND", 404, False),
        ("REPLAY_DETECTED", 409, False),
        ("MFA_RESEND_COOLDOWN", 409, True),
        ("INVALID_REQUEST", 422, False),
        ("CHANNEL_GATEWAY_UNAUTHORIZED", 401, False),
        ("CHANNEL_MESSAGE_CONFLICT", 409, False),
        ("CHANNEL_MESSAGE_IN_PROGRESS", 409, True),
        ("CHANNEL_PROCESSING_FAILED", 503, True),
        ("TASK_NODE_INPUT_TAMPERED", 409, False),
        ("TASK_NODE_RECEIPT_REQUIRED", 409, False),
        ("TASK_GRAPH_CYCLE_DETECTED", 409, False),
        ("TASK_GRAPH_TOOL_NOT_ALLOWED", 409, False),
        ("TASK_GRAPH_WRITE_AUTHORIZATION_REQUIRED", 409, False),
        ("INTERNAL_ERROR", 500, True),
    ],
)
def test_error_catalog_exposes_stable_public_contract(
    code: str,
    status_code: int,
    retryable: bool,
) -> None:
    error = ApplicationError(code, details={"request_id": "req_demo"})

    assert error.status_code == status_code
    assert error.retryable is retryable
    assert error.public_detail() == {
        "code": code,
        "message": error_definition(code).message,
        "retryable": retryable,
        "details": {"request_id": "req_demo"},
    }


def test_unknown_error_code_uses_non_retryable_state_conflict() -> None:
    error = ApplicationError("UNKNOWN_BUSINESS_STATE")

    assert error.status_code == 409
    assert error.retryable is False
    assert error.public_detail()["message"] == "当前状态下无法完成该请求。"
