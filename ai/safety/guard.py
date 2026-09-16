INJECTION_HINTS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "bypass",
    "do not ask confirmation",
)


def evaluate_text(text: str) -> dict:
    lowered = text.lower()
    matched = [hint for hint in INJECTION_HINTS if hint in lowered]

    return {
        "safe": not matched,
        "risk_type": "PROMPT_INJECTION" if matched else "NONE",
        "score": 90 if matched else 0,
        "reason": "Matched prompt injection hints." if matched else "No obvious risk detected.",
    }

