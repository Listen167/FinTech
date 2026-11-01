"""输入安全检测。

比赛版本采用确定性规则兜底。大模型可以补充风险线索，但不能覆盖这里的拦截结果。
"""

from __future__ import annotations

import re
import unicodedata


INJECTION_PATTERNS = {
    "PROMPT_OVERRIDE": (
        r"ignore\s+(all\s+)?previous",
        r"忽略(之前|以上|所有).{0,8}(指令|规则|要求)",
        r"你现在是.{0,12}(管理员|开发者|系统)",
        r"泄露.{0,8}(系统提示|system prompt)",
    ),
    "VERIFICATION_BYPASS": (
        r"(跳过|绕过|不要|无需).{0,8}(确认|验证|验证码|风控)",
        r"do\s+not\s+ask\s+(for\s+)?confirmation",
        r"bypass.{0,12}(confirmation|verification|policy)",
        r"直接转.{0,8}(不要问|无需确认)",
    ),
}

FRAUD_PATTERNS = {
    "SAFE_ACCOUNT": r"安全账户|监管账户|核查账户",
    "IMPERSONATION": r"公检法|公安|法院|客服.{0,8}(要求|让我|说)",
    "URGENCY": r"马上转|立即转|十分钟内|超时.{0,6}(冻结|失效)",
    "SECRECY": r"不要告诉|必须保密|不能告诉家人",
    "REMOTE_CONTROL": r"屏幕共享|远程控制|下载.{0,8}(会议|投屏|控制)",
}


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", normalized).strip()


def evaluate_text(text: str) -> dict:
    """返回硬拦截结果和可供策略引擎使用的诈骗语义线索。"""

    normalized = _normalize(text)
    injection_matches: list[str] = []
    fraud_hints: list[str] = []

    for category, patterns in INJECTION_PATTERNS.items():
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns):
            injection_matches.append(category)

    for category, pattern in FRAUD_PATTERNS.items():
        if re.search(pattern, normalized, re.IGNORECASE):
            fraud_hints.append(category)

    blocked = bool(injection_matches)
    score = min(100, len(injection_matches) * 70 + len(fraud_hints) * 18)
    return {
        "safe": not blocked,
        "blocked": blocked,
        "risk_type": injection_matches[0] if injection_matches else "NONE",
        "score": score,
        "matched_rules": injection_matches,
        "risk_hints": fraud_hints,
        "reason": (
            "检测到试图覆盖系统规则或绕过验证的指令。"
            if blocked
            else "输入未触发硬拦截规则。"
        ),
    }

