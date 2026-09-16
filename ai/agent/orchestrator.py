from ai.safety.guard import evaluate_text


def handle_message(session_id: str, message: str) -> dict:
    safety = evaluate_text(message)
    if not safety["safe"]:
        return {
            "session_id": session_id,
            "type": "BLOCKED",
            "message": "Request blocked by safety guard.",
            "data": safety,
        }

    return {
        "session_id": session_id,
        "type": "TEXT",
        "message": "AI orchestrator scaffold is ready.",
    }

