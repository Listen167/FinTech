"""主动生活事件雷达：重读事实、最小披露投递与失败收敛。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

from backend.app.errors import ApplicationError
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.repositories.life_event_radar import LifeEventRadarRepository
from backend.app.security.channel_identity_cipher import ChannelIdentityCipher


class ProactiveMessageSender(Protocol):
    def send_text(
        self,
        *,
        receive_id: str,
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


class LifeEventRadarService:
    SUPPORTED_EVENT_TYPES = {"BIRTHDAY", "ANNIVERSARY", "TUITION"}
    ACTIVE_GOAL_STATUSES = {"DRAFT", "ACTIVE", "CONFIRMED", "FUNDED"}

    def __init__(
        self,
        repository: LifeEventRadarRepository,
        channel_repository: ChannelGatewayRepository,
        *,
        identity_cipher: ChannelIdentityCipher | None,
        sender: ProactiveMessageSender | None,
        trusted_app_base_url: str,
        batch_size: int = 20,
        lease_seconds: int = 120,
        simulation_enabled: bool = False,
    ) -> None:
        self.repository = repository
        self.channel_repository = channel_repository
        self.identity_cipher = identity_cipher
        self.sender = sender
        self.trusted_app_base_url = trusted_app_base_url.rstrip("/")
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.simulation_enabled = simulation_enabled

    @property
    def delivery_configured(self) -> bool:
        return self.sender is not None and self.identity_cipher is not None

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.delivery_configured,
            "simulation_enabled": self.simulation_enabled,
            "clock_mode": "REAL_TIME",
            "supported_event_types": sorted(self.SUPPORTED_EVENT_TYPES),
            "delivery_channel": "FEISHU_PRIVATE_CHAT",
            "funds_execution_allowed": False,
        }

    def list_actions(self, owner_user_id: str) -> list[dict[str, Any]]:
        return self.repository.list_actions(owner_user_id)

    def list_audits(self, owner_user_id: str, action_id: str) -> list[dict[str, Any]]:
        return self.repository.list_audits(owner_user_id, action_id)

    def control_action(
        self,
        *,
        owner_user_id: str,
        action_id: str,
        command: str,
    ) -> dict[str, Any]:
        return self.repository.transition_user_action(
            owner_user_id=owner_user_id,
            action_id=action_id,
            command=command,
            now=datetime.now(timezone.utc),
        )

    def scan_once(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
        owner_user_id: str | None = None,
        clock_mode: str = "REAL_TIME",
    ) -> dict[str, Any]:
        if not self.delivery_configured:
            raise ApplicationError("LIFE_RADAR_NOT_CONFIGURED")
        effective_now = now or datetime.now(timezone.utc)
        actions = self.repository.claim_due_actions(
            worker_id=worker_id,
            now=effective_now,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
            owner_user_id=owner_user_id,
        )
        results = [self._process_action(action, worker_id, effective_now) for action in actions]
        return {
            "clock": {"mode": clock_mode, "effective_now": effective_now.isoformat()},
            "claimed": len(actions),
            "sent": sum(item["status"] == "SENT" for item in results),
            "failed": sum(item["status"] in {"FAILED", "MANUAL_REVIEW"} for item in results),
            "canceled": sum(item["status"] == "CANCELED" for item in results),
            "items": results,
        }

    def simulate_scan(self, *, owner_user_id: str, advance_days: int) -> dict[str, Any]:
        if not self.simulation_enabled:
            raise ApplicationError("LIFE_RADAR_SIMULATION_DISABLED")
        effective_now = datetime.now(timezone.utc) + timedelta(days=advance_days)
        return self.scan_once(
            worker_id=f"sandbox:{owner_user_id}",
            now=effective_now,
            owner_user_id=owner_user_id,
            clock_mode="SANDBOX_SIMULATED_TIME",
        )

    def _process_action(
        self,
        action: dict[str, Any],
        worker_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        action_id = action["action_id"]
        try:
            context = self.repository.load_delivery_context(action_id, action["owner_user_id"])
            source_error = self._source_error(context)
            if source_error:
                return self.repository.cancel_from_worker(
                    action_id=action_id,
                    worker_id=worker_id,
                    reason_code=source_error,
                    now=now,
                )
            identity = self.channel_repository.get_active_delivery_identity(
                user_id=action["owner_user_id"], channel="FEISHU"
            )
            if identity is None or self.identity_cipher is None:
                raise ApplicationError("FEISHU_DELIVERY_ID_UNAVAILABLE")
            receive_id = self.identity_cipher.decrypt(identity["external_user_id_ciphertext"])
            funding_state = self._funding_state(context)
            deep_link = f"{self.trusted_app_base_url}/?{urlencode({'workspace': 'life', 'action_id': action_id})}"
            text = (
                "生活事件雷达提醒\n"
                "你有一项生活资金事件即将到期，当前资金准备状态需要关注。\n"
                f"请返回安心办查看最新目标、余额与处理建议：{deep_link}\n"
                "本提醒不能执行转账、扣款或代替安全验证。"
            )
            provider = self.sender.send_text(
                receive_id=receive_id,
                text=text,
                idempotency_key=action_id,
            )
            receipt = {
                "channel": "FEISHU",
                "status": "DELIVERED",
                "delivered_at": now.isoformat(),
                "provider_message_id": provider.get("message_id"),
                "funding_state": funding_state,
                "source_versions": {
                    "event_updated_at": (context.get("event") or {}).get("updated_at"),
                    "goal_updated_at": (context.get("goal") or {}).get("updated_at"),
                },
            }
            return self.repository.mark_sent(
                action_id=action_id,
                worker_id=worker_id,
                receipt=receipt,
                now=now,
            )
        except ApplicationError as error:
            return self.repository.mark_failed(
                action_id=action_id,
                worker_id=worker_id,
                error_code=error.code,
                now=now,
            )
        except Exception:
            return self.repository.mark_failed(
                action_id=action_id,
                worker_id=worker_id,
                error_code="FEISHU_PROACTIVE_SEND_FAILED",
                now=now,
            )

    def _source_error(self, context: dict[str, Any]) -> str | None:
        event = context.get("event")
        goal = context.get("goal")
        if event is not None:
            if event["status"] != "ACTIVE" or event["node_status"] != "ACTIVE":
                return "LIFE_EVENT_INACTIVE"
            if event["event_type"] not in self.SUPPORTED_EVENT_TYPES and not (goal and goal.get("due_at")):
                return "LIFE_EVENT_TYPE_UNSUPPORTED"
        if goal is not None and (
            goal["status"] not in self.ACTIVE_GOAL_STATUSES or goal["node_status"] != "ACTIVE"
        ):
            return "FINANCIAL_GOAL_INACTIVE"
        if event is None and goal is None:
            return "LIFE_EVENT_SOURCE_MISSING"
        return None

    @staticmethod
    def _funding_state(context: dict[str, Any]) -> str:
        goal = context.get("goal")
        if goal is None:
            return "REVIEW_REQUIRED"
        remaining = max(
            Decimal(goal["target_amount"]) - Decimal(goal["reserved_amount"]),
            Decimal("0.00"),
        )
        return "AVAILABLE" if Decimal(context["available_balance"]) >= remaining else "ATTENTION"
