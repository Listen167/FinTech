"""跨 Run 反诈冷静期：风险记忆、同收款人阻断、申诉与受信 App MFA 释放。"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

from sqlalchemy import Engine

from backend.app.core.config import settings
from backend.app.errors import ApplicationError
from backend.app.repositories.fraud_cooling import (
    FraudRepositoryError,
    MemoryFraudCoolingRepository,
    MySQLFraudCoolingRepository,
    NewFraudReleaseChallenge,
    NewFraudRiskEvent,
)
from backend.app.services.mfa_service import MfaCodeService
from backend.app.services.policy_engine import POLICY_VERSION


FRAUD_SIGNAL_FACTS = {
    "SAFE_ACCOUNT": "出现“安全账户、监管账户或核查账户”等诱导转账话术。",
    "IMPERSONATION": "出现冒充公检法、平台客服或权威机构的表述。",
    "URGENCY": "出现立即转账、限时冻结或制造紧迫感的表述。",
    "SECRECY": "出现要求保密、不告知家人等隔离受害人的表述。",
    "REMOTE_CONTROL": "出现屏幕共享、远程控制或安装控制软件的表述。",
}
CRITICAL_SIGNALS = {"SAFE_ACCOUNT", "REMOTE_CONTROL"}
SEMANTIC_SIGNALS = set(FRAUD_SIGNAL_FACTS)


class FraudCoolingError(ApplicationError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", normalized).strip()


def _mask_name(value: str) -> str:
    clean = value.strip()
    if len(clean) <= 1:
        return "*"
    if len(clean) == 2:
        return f"{clean[0]}*"
    return f"{clean[0]}{'*' * min(3, len(clean) - 2)}{clean[-1]}"


class FraudCoolingService:
    def __init__(
        self,
        *,
        engine: Engine | None = None,
        repository: MemoryFraudCoolingRepository | MySQLFraudCoolingRepository | None = None,
        mfa: MfaCodeService | None = None,
        pepper: str | None = None,
        cooling_minutes: int | None = None,
        challenge_ttl_seconds: int | None = None,
        max_attempts: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository or (
            MySQLFraudCoolingRepository(engine)
            if engine is not None
            else MemoryFraudCoolingRepository()
        )
        configured = settings.fraud_recipient_pepper or settings.jwt_secret_key
        self._pepper = (
            pepper
            or (configured.get_secret_value() if configured else None)
            or secrets.token_urlsafe(48)
        ).encode("utf-8")
        self.mfa = mfa or MfaCodeService()
        self.cooling_minutes = cooling_minutes or settings.fraud_cooling_minutes
        self.challenge_ttl_seconds = (
            challenge_ttl_seconds or settings.fraud_release_challenge_ttl_seconds
        )
        self.max_attempts = max_attempts or settings.fraud_release_max_attempts
        self.clock = clock or _utc_now

    def _now(self) -> datetime:
        return _naive_utc(self.clock())

    def fingerprint_recipient(self, owner_user_id: str, recipient: dict[str, Any]) -> str:
        canonical = "|".join((
            "recipient-v1",
            _normalize(owner_user_id),
            _normalize(str(recipient.get("name", ""))),
            _normalize(str(recipient.get("account_masked", ""))),
        ))
        return hmac.new(self._pepper, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def should_start(signals: list[str], policy_factors: list[str]) -> bool:
        signal_set = set(signals) & SEMANTIC_SIGNALS
        factor_set = set(policy_factors)
        if signal_set & CRITICAL_SIGNALS:
            return True
        if len(signal_set) >= 2:
            return True
        return bool(
            signal_set
            and {"NEW_RECIPIENT", "AMOUNT_OVER_1000"}.issubset(factor_set)
        )

    def evaluate_transfer(
        self,
        *,
        owner_user_id: str,
        run_id: str | None,
        recipient: dict[str, Any],
        risk_hints: list[str],
        policy_factors: list[str],
        message: str,
    ) -> dict[str, Any] | None:
        now = self._now()
        fingerprint = self.fingerprint_recipient(owner_user_id, recipient)
        active = self.repository.block_active_retry(
            owner_user_id, fingerprint, run_id, now
        )
        if active is not None:
            return self._public_event(active)

        signals = sorted(set(risk_hints) & SEMANTIC_SIGNALS)
        if not self.should_start(signals, policy_factors):
            return None
        evidence = [
            {
                "signal": signal,
                "fact": FRAUD_SIGNAL_FACTS[signal],
                "source": "DETERMINISTIC_RULE",
            }
            for signal in signals
        ]
        _, row = self.repository.trigger_or_reuse(NewFraudRiskEvent(
            event_id=f"fraud_{uuid4().hex[:16]}",
            owner_user_id=owner_user_id,
            trigger_run_id=run_id,
            recipient_fingerprint=fingerprint,
            recipient_display=_mask_name(str(recipient.get("name", "未知收款人"))),
            recipient_account_masked=recipient.get("account_masked"),
            signals=signals,
            evidence=evidence,
            policy_version=POLICY_VERSION,
            message_digest=hashlib.sha256(
                unicodedata.normalize("NFKC", message).encode("utf-8")
            ).hexdigest(),
            triggered_at=now,
            cooling_until=now + timedelta(minutes=self.cooling_minutes),
        ))
        return self._public_event(row)

    def active_for_recipient(
        self,
        *,
        owner_user_id: str,
        run_id: str | None,
        recipient: dict[str, Any],
    ) -> dict[str, Any] | None:
        fingerprint = self.fingerprint_recipient(owner_user_id, recipient)
        row = self.repository.block_active_retry(
            owner_user_id, fingerprint, run_id, self._now()
        )
        return self._public_event(row) if row is not None else None

    def get(self, owner_user_id: str, event_id: str) -> dict[str, Any]:
        row = self.repository.get(owner_user_id, event_id, self._now())
        if row is None:
            if self.repository.exists_for_other_owner(owner_user_id, event_id):
                raise FraudCoolingError("RESOURCE_FORBIDDEN")
            raise FraudCoolingError("FRAUD_EVENT_NOT_FOUND")
        return self._public_event(row)

    def list(self, owner_user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return [
            self._public_event(row)
            for row in self.repository.list(owner_user_id, status, self._now())
        ]

    def audits(self, owner_user_id: str, event_id: str) -> list[dict[str, Any]]:
        self.get(owner_user_id, event_id)
        return [self._public_audit(row) for row in self.repository.audits(owner_user_id, event_id)]

    def submit_appeal(
        self, *, owner_user_id: str, event_id: str, reason: str,
    ) -> dict[str, Any]:
        try:
            row = self.repository.submit_appeal(
                owner_user_id, event_id, reason, self._now()
            )
        except FraudRepositoryError as error:
            raise FraudCoolingError(str(error)) from error
        return self._public_event(row)

    def create_release_challenge(
        self,
        *,
        owner_user_id: str,
        event_id: str,
        channel: Literal["TRUSTED_APP", "FEISHU", "IM"],
    ) -> dict[str, Any]:
        self._require_trusted_app(channel)
        now = self._now()
        challenge_id = f"fraud_verify_{uuid4().hex[:16]}"
        otp_hash = self.mfa.issue(challenge_id)
        try:
            row = self.repository.create_challenge(NewFraudReleaseChallenge(
                challenge_id=challenge_id,
                event_id=event_id,
                owner_user_id=owner_user_id,
                otp_hash=otp_hash,
                issued_at=now,
                expires_at=now + timedelta(seconds=self.challenge_ttl_seconds),
            ))
        except FraudRepositoryError as error:
            self.mfa.discard(challenge_id)
            raise FraudCoolingError(str(error)) from error
        return self._public_challenge(row)

    def verify_release(
        self,
        *,
        owner_user_id: str,
        challenge_id: str,
        code: str,
        channel: Literal["TRUSTED_APP", "FEISHU", "IM"],
    ) -> dict[str, Any]:
        self._require_trusted_app(channel)
        now = self._now()
        challenge = self.repository.get_challenge(owner_user_id, challenge_id, now)
        if challenge is None:
            raise FraudCoolingError("FRAUD_RELEASE_CHALLENGE_NOT_FOUND")
        if challenge["status"] != "PENDING":
            if challenge["status"] == "EXPIRED":
                raise FraudCoolingError("FRAUD_RELEASE_CHALLENGE_EXPIRED")
            raise FraudCoolingError("FRAUD_RELEASE_CHALLENGE_NOT_PENDING")
        if not self.mfa.matches(challenge_id, code, challenge["otp_hash"]):
            try:
                _, failed = self.repository.fail_challenge(
                    owner_user_id, challenge_id, now, self.max_attempts
                )
            except FraudRepositoryError as error:
                raise FraudCoolingError(str(error)) from error
            if failed["status"] == "LOCKED":
                self.mfa.discard(challenge_id)
                raise FraudCoolingError("FRAUD_RELEASE_VERIFICATION_LOCKED")
            raise FraudCoolingError(
                "FRAUD_RELEASE_VERIFICATION_FAILED",
                details={
                    "remaining_attempts": self.max_attempts - failed["failed_attempts"]
                },
            )
        try:
            event, verified = self.repository.release(
                owner_user_id, challenge_id, now
            )
        except FraudRepositoryError as error:
            raise FraudCoolingError(str(error)) from error
        self.mfa.discard(challenge_id)
        return {
            "event": self._public_event(event),
            "challenge": self._public_challenge(verified),
            "funds_executed": False,
            "message": "冷静期已通过受信 App 强验证解除；原转账不会自动恢复，请重新发起并再次接受风控。",
        }

    def get_demo_code(self, *, owner_user_id: str, challenge_id: str) -> dict[str, Any]:
        challenge = self.repository.get_challenge(owner_user_id, challenge_id, self._now())
        if challenge is None:
            raise FraudCoolingError("FRAUD_RELEASE_CHALLENGE_NOT_FOUND")
        if challenge["status"] != "PENDING":
            raise FraudCoolingError("FRAUD_RELEASE_CHALLENGE_NOT_PENDING")
        code = self.mfa.demo_code(challenge_id)
        if code is None:
            raise FraudCoolingError("DEMO_CODE_UNAVAILABLE")
        return {
            "challenge_id": challenge_id,
            "code": code,
            "delivery_channel": "MOCK_SMS",
            "expires_at": _iso_utc(challenge["expires_at"]),
        }

    def reset_owner(self, owner_user_id: str) -> None:
        self.repository.reset_owner(owner_user_id)

    @staticmethod
    def _require_trusted_app(channel: str) -> None:
        if channel != "TRUSTED_APP":
            raise FraudCoolingError("FRAUD_RELEASE_CHANNEL_FORBIDDEN")

    @staticmethod
    def _public_event(row: dict[str, Any]) -> dict[str, Any]:
        fingerprint = row["recipient_fingerprint"]
        return {
            "event_id": row["event_id"],
            "trigger_run_id": row.get("trigger_run_id"),
            "status": row["status"],
            "recipient": {
                "display_name": row["recipient_display"],
                "account_masked": row.get("recipient_account_masked"),
                "fingerprint_hint": f"{fingerprint[:8]}...{fingerprint[-6:]}",
            },
            "signals": list(row["signals"]),
            "evidence": list(row["evidence"]),
            "policy_version": row["policy_version"],
            "retry_count": row["retry_count"],
            "appeal": {
                "status": row["appeal_status"],
                "reason": row.get("appeal_reason"),
                "submitted_at": _iso_utc(row.get("appeal_submitted_at")),
            },
            "triggered_at": _iso_utc(row["triggered_at"]),
            "cooling_until": _iso_utc(row["cooling_until"]),
            "released_at": _iso_utc(row.get("released_at")),
            "release_method": row.get("release_method"),
            "release": {
                "trusted_app_only": True,
                "strong_verification_required": True,
                "channel_release_allowed": False,
                "funds_auto_resume": False,
            },
            "help": {
                "title": "停止转账并通过官方渠道核实",
                "hotline": "96110",
                "steps": [
                    "不要继续屏幕共享或提供验证码。",
                    "通过银行官方电话核实账户和交易。",
                    "如已转账或泄露信息，请立即拨打 96110 或 110。",
                ],
            },
        }

    @staticmethod
    def _public_challenge(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "challenge_id": row["challenge_id"],
            "event_id": row["event_id"],
            "status": row["status"],
            "failed_attempts": row["failed_attempts"],
            "delivery_channel": row["delivery_channel"],
            "delivery_hint": "账户预留手机号",
            "issued_at": _iso_utc(row["issued_at"]),
            "expires_at": _iso_utc(row["expires_at"]),
            "verified_at": _iso_utc(row.get("verified_at")),
        }

    @staticmethod
    def _public_audit(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "audit_id": row["audit_id"],
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "actor": row["actor"],
            "details": row["details"],
            "created_at": _iso_utc(row["created_at"]),
        }

