"""统一渠道消息编排，不授予 IM 资金确认或强验证权限。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from urllib.parse import urlencode

from backend.app.errors import ApplicationError, error_definition
from backend.app.repositories.channel_gateway import ChannelGatewayRepository
from backend.app.schemas.channel import ChannelWorkerHeartbeat, InboundChannelMessage
from backend.app.security.channel_identity_cipher import ChannelIdentityCipher
from backend.app.services.agent_runtime import AgentRuntime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def summarize_channel_status(
    workers: list[dict[str, Any]],
    *,
    gateway_configured: bool,
    feishu_configured: bool,
    webhook_configured: bool,
) -> dict[str, Any]:
    transports = []
    for name, configured in (
        ("LONG_CONNECTION", gateway_configured and feishu_configured),
        ("WEBHOOK", webhook_configured),
        ("SCHEDULER", gateway_configured and feishu_configured),
    ):
        matching = [item for item in workers if item["transport"] == name]
        online_workers = [item for item in matching if item["online"]]
        transports.append({
            "transport": name,
            "configured": configured,
            "online": bool(online_workers),
            "worker_count": len(online_workers),
            "last_heartbeat_at": matching[0]["heartbeat_at"] if matching else None,
        })
    return {
        "gateway_configured": gateway_configured,
        "feishu_configured": feishu_configured,
        "webhook_configured": webhook_configured,
        "online": any(
            item["online"] and item["worker_kind"] == "FEISHU_INBOUND"
            for item in workers
        ),
        "transports": transports,
        "workers": workers,
    }


class ChannelGatewayService:
    LINK_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

    def __init__(
        self,
        repository: ChannelGatewayRepository,
        agent_runtime: AgentRuntime,
        *,
        identity_pepper: str,
        link_token_ttl_seconds: int = 600,
        trusted_app_base_url: str = "http://127.0.0.1:5173",
    ) -> None:
        if len(identity_pepper) < 32:
            raise ValueError("CHANNEL_IDENTITY_PEPPER_TOO_SHORT")
        self.repository = repository
        self.agent_runtime = agent_runtime
        self.identity_pepper = identity_pepper.encode("utf-8")
        self.identity_cipher = ChannelIdentityCipher(identity_pepper)
        self.link_token_ttl_seconds = link_token_ttl_seconds
        self.trusted_app_base_url = trusted_app_base_url.rstrip("/")
        self._conversation_lock = RLock()

    @staticmethod
    def _normalize_link_code(value: str) -> str:
        return "".join(character for character in value.upper() if character.isalnum())

    def _hmac(self, namespace: str, *values: str) -> str:
        value = "\x1f".join((namespace, *values)).encode("utf-8")
        return hmac.new(self.identity_pepper, value, hashlib.sha256).hexdigest()

    def _token_hash(self, channel: str, link_code: str) -> str:
        return self._hmac("link-token", channel, self._normalize_link_code(link_code))

    def create_link_token(self, *, user_id: str, channel: str) -> dict[str, Any]:
        raw = "".join(secrets.choice(self.LINK_ALPHABET) for _ in range(8))
        display_code = f"{raw[:4]}-{raw[4:]}"
        expires_at = _now() + timedelta(seconds=self.link_token_ttl_seconds)
        record = self.repository.create_link_token(
            user_id=user_id,
            channel=channel,
            token_hash=self._token_hash(channel, raw),
            expires_at=expires_at.replace(tzinfo=None),
        )
        return {
            **record,
            "link_code": display_code,
            "instructions": "请在目标渠道的已验签会话中提交该绑定码。绑定码只能使用一次。",
        }

    def list_identities(self, *, user_id: str) -> list[dict[str, Any]]:
        public_fields = {
            "identity_id",
            "channel",
            "external_user_fingerprint",
            "status",
            "linked_at",
            "last_active_at",
            "revoked_at",
        }
        return [
            {key: value for key, value in item.items() if key in public_fields}
            for item in self.repository.list_identities(user_id)
        ]

    def revoke_identity(self, *, user_id: str, identity_id: str) -> dict[str, Any]:
        return self.repository.revoke_identity(user_id=user_id, identity_id=identity_id)

    def record_worker_heartbeat(
        self,
        payload: ChannelWorkerHeartbeat,
        *,
        default_lease_seconds: int,
    ) -> dict[str, Any]:
        return self.repository.record_worker_heartbeat(
            worker_id=payload.worker_id,
            worker_kind=payload.worker_kind,
            transport=payload.transport,
            state=payload.state,
            lease_seconds=payload.lease_seconds or default_lease_seconds,
            last_error_code=payload.last_error_code,
        )

    def management_status(
        self,
        *,
        gateway_configured: bool,
        feishu_configured: bool,
        webhook_configured: bool,
    ) -> dict[str, Any]:
        return summarize_channel_status(
            self.repository.list_worker_statuses(),
            gateway_configured=gateway_configured,
            feishu_configured=feishu_configured,
            webhook_configured=webhook_configured,
        )

    def process_inbound(self, payload: InboundChannelMessage) -> dict[str, Any]:
        external_user_ciphertext = self.identity_cipher.encrypt(payload.external_user_id)
        external_user_hash = self._hmac(
            "external-user", payload.channel, payload.tenant_id, payload.external_user_id
        )
        external_message_hash = self._hmac(
            "external-message",
            payload.channel,
            payload.tenant_id,
            payload.external_message_id,
        )
        external_conversation_hash = self._hmac(
            "external-conversation",
            payload.channel,
            payload.tenant_id,
            payload.external_conversation_id,
        )
        canonical_payload = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = self._hmac("message-payload", canonical_payload)
        message, created = self.repository.begin_message(
            channel=payload.channel,
            tenant_id=payload.tenant_id,
            external_message_id_hash=external_message_hash,
            payload_hash=payload_hash,
            message_kind=payload.kind,
        )
        if not created:
            if message["status"] in {"PROCESSED", "REJECTED"}:
                return {**message["response_payload"], "replayed": True}
            if message["status"] == "FAILED":
                raise ApplicationError(
                    "CHANNEL_MESSAGE_PREVIOUSLY_FAILED",
                    details={"gateway_message_id": message["gateway_message_id"]},
                )
            raise ApplicationError(
                "CHANNEL_MESSAGE_IN_PROGRESS",
                details={"gateway_message_id": message["gateway_message_id"]},
            )

        gateway_message_id = message["gateway_message_id"]
        try:
            if payload.kind == "LINK":
                return self._process_link(
                    payload,
                    gateway_message_id=gateway_message_id,
                    external_user_hash=external_user_hash,
                    external_user_ciphertext=external_user_ciphertext,
                )
            return self._process_text(
                payload,
                gateway_message_id=gateway_message_id,
                external_user_hash=external_user_hash,
                external_conversation_hash=external_conversation_hash,
                external_user_ciphertext=external_user_ciphertext,
            )
        except ApplicationError as error:
            response = self._error_response(
                gateway_message_id,
                error.code,
                error.message,
                error.retryable,
            )
            self.repository.complete_message(
                gateway_message_id=gateway_message_id,
                status="FAILED",
                response_payload=response,
                error_code=error.code,
            )
            raise
        except Exception as error:
            public_error = ApplicationError(
                "CHANNEL_PROCESSING_FAILED",
                details={"gateway_message_id": gateway_message_id},
            )
            response = self._error_response(
                gateway_message_id,
                public_error.code,
                public_error.message,
                public_error.retryable,
            )
            self.repository.complete_message(
                gateway_message_id=gateway_message_id,
                status="FAILED",
                response_payload=response,
                error_code=public_error.code,
            )
            raise public_error from error

    def _process_link(
        self,
        payload: InboundChannelMessage,
        *,
        gateway_message_id: str,
        external_user_hash: str,
        external_user_ciphertext: str,
    ) -> dict[str, Any]:
        try:
            identity = self.repository.redeem_link_token(
                token_hash=self._token_hash(payload.channel, payload.link_code or ""),
                channel=payload.channel,
                tenant_id=payload.tenant_id,
                external_user_id_hash=external_user_hash,
                external_user_id_ciphertext=external_user_ciphertext,
            )
        except ApplicationError as error:
            response = self._error_response(
                gateway_message_id,
                error.code,
                error.message,
                error.retryable,
            )
            self.repository.complete_message(
                gateway_message_id=gateway_message_id,
                status="REJECTED",
                response_payload=response,
                error_code=error.code,
            )
            return response

        response = {
            "schema_version": "1.0",
            "gateway_message_id": gateway_message_id,
            "status": "PROCESSED",
            "replayed": False,
            "reply": {"type": "TEXT", "text": "渠道身份绑定成功，可以开始使用银行智能体。"},
            "binding": {
                "identity_id": identity["identity_id"],
                "channel": identity["channel"],
                "status": identity["status"],
            },
            "agent": None,
            "security": {"mode": "CHANNEL_ONLY", "action": "NONE"},
        }
        self.repository.complete_message(
            gateway_message_id=gateway_message_id,
            status="PROCESSED",
            response_payload=response,
            identity_id=identity["identity_id"],
        )
        return response

    def _process_text(
        self,
        payload: InboundChannelMessage,
        *,
        gateway_message_id: str,
        external_user_hash: str,
        external_conversation_hash: str,
        external_user_ciphertext: str,
    ) -> dict[str, Any]:
        identity = self.repository.resolve_identity(
            channel=payload.channel,
            tenant_id=payload.tenant_id,
            external_user_id_hash=external_user_hash,
        )
        if identity is None:
            definition = error_definition("CHANNEL_BINDING_REQUIRED")
            response = self._error_response(
                gateway_message_id,
                "CHANNEL_BINDING_REQUIRED",
                definition.message,
                definition.retryable,
            )
            response["reply"]["text"] = "当前渠道尚未绑定，请先在银行 Web/App 中生成一次性绑定码。"
            self.repository.complete_message(
                gateway_message_id=gateway_message_id,
                status="REJECTED",
                response_payload=response,
                error_code="CHANNEL_BINDING_REQUIRED",
            )
            return response

        # 兼容升级前的旧绑定：用户再次发消息时补齐可逆投递标识。
        self.repository.update_delivery_identity_ciphertext(
            identity_id=identity["identity_id"],
            external_user_id_ciphertext=external_user_ciphertext,
        )

        with self._conversation_lock:
            conversation = self.repository.get_conversation(
                identity_id=identity["identity_id"],
                external_conversation_id_hash=external_conversation_hash,
            )
            if conversation is None:
                session = self.agent_runtime.create_session(
                    user_id=identity["user_id"], channel=payload.channel
                )
                conversation = self.repository.create_conversation(
                    identity_id=identity["identity_id"],
                    external_conversation_id_hash=external_conversation_hash,
                    agent_session_id=session["session_id"],
                )
            else:
                conversation = self._recover_stale_conversation(
                    conversation,
                    identity_id=identity["identity_id"],
                    user_id=identity["user_id"],
                    channel=payload.channel,
                    external_conversation_hash=external_conversation_hash,
                )

        try:
            run = self.agent_runtime.create_run(
                conversation["agent_session_id"],
                payload.text or "",
                f"channel:{gateway_message_id}",
                user_id=identity["user_id"],
            )
        except KeyError as error:
            if error.args != ("SESSION_NOT_FOUND",):
                raise
            with self._conversation_lock:
                conversation = self._replace_agent_session(
                    identity_id=identity["identity_id"],
                    user_id=identity["user_id"],
                    channel=payload.channel,
                    external_conversation_hash=external_conversation_hash,
                )
            run = self.agent_runtime.create_run(
                conversation["agent_session_id"],
                payload.text or "",
                f"channel:{gateway_message_id}",
                user_id=identity["user_id"],
            )
        response = self._agent_response(gateway_message_id, run)
        self.repository.complete_message(
            gateway_message_id=gateway_message_id,
            status="PROCESSED",
            response_payload=response,
            identity_id=identity["identity_id"],
            conversation_id=conversation["conversation_id"],
            run_id=run["run_id"],
        )
        return response

    def _recover_stale_conversation(
        self,
        conversation: dict[str, Any],
        *,
        identity_id: str,
        user_id: str,
        channel: str,
        external_conversation_hash: str,
    ) -> dict[str, Any]:
        try:
            self.agent_runtime.get_session(
                conversation["agent_session_id"], user_id=user_id
            )
            return conversation
        except KeyError as error:
            if error.args != ("SESSION_NOT_FOUND",):
                raise
            return self._replace_agent_session(
                identity_id=identity_id,
                user_id=user_id,
                channel=channel,
                external_conversation_hash=external_conversation_hash,
            )

    def _replace_agent_session(
        self,
        *,
        identity_id: str,
        user_id: str,
        channel: str,
        external_conversation_hash: str,
    ) -> dict[str, Any]:
        session = self.agent_runtime.create_session(user_id=user_id, channel=channel)
        return self.repository.rebind_conversation(
            identity_id=identity_id,
            external_conversation_id_hash=external_conversation_hash,
            agent_session_id=session["session_id"],
        )

    def _agent_response(self, gateway_message_id: str, run: dict[str, Any]) -> dict[str, Any]:
        pending_type = run.get("pending_action", {}).get("type", "NONE")
        secure_actions = {
            "CONFIRMATION": ("OPEN_APP_CONFIRMATION", "confirmation", "前往安全确认"),
            "VERIFICATION": ("OPEN_APP_VERIFICATION", "verification", "前往强验证"),
            "LIFE_GRAPH_CHANGE_REQUEST": (
                "OPEN_APP_CONFIRMATION",
                "life_graph",
                "查看并确认方案",
            ),
        }
        security = {"mode": "CHANNEL_ONLY", "action": "NONE"}
        secure_action = secure_actions.get(pending_type)
        if secure_action is not None:
            action, action_type, action_label = secure_action
            pending = run.get("pending_action", {})
            pending_detail = (
                pending.get("confirmation")
                or pending.get("verification")
                or pending.get("life_graph_change_request")
                or {}
            )
            query = urlencode({"secure_action": action_type, "run_id": run["run_id"]})
            security = {
                "mode": "APP_REQUIRED",
                "action": action,
                "run_id": run["run_id"],
                "deep_link": f"{self.trusted_app_base_url}/?{query}",
                "action_label": action_label,
                "risk_level": run.get("policy", {}).get("level", "YELLOW"),
                "summary": pending_detail.get("summary") or run["message"],
                "expires_at": pending_detail.get("expires_at"),
                "reason": "渠道消息不能代替受信 App 内的确认或强验证。",
            }
        elif run.get("state") == "COOLING_OFF":
            event = run.get("data", {}).get("fraud_cooling_event") or {}
            event_id = event.get("event_id")
            query = urlencode({"fraud_event": event_id}) if event_id else ""
            security = {
                "mode": "APP_REQUIRED",
                "action": "OPEN_APP_FRAUD_REVIEW",
                "run_id": run["run_id"],
                "deep_link": f"{self.trusted_app_base_url}/?{query}",
                "action_label": "前往银行 App 核实",
                "risk_level": "BLOCKED",
                "summary": run["message"],
                "expires_at": event.get("cooling_until"),
                "reason": "飞书只能接收风险提醒，不能申诉或解除反诈冷静期。",
            }
        return {
            "schema_version": "1.0",
            "gateway_message_id": gateway_message_id,
            "status": "PROCESSED",
            "replayed": False,
            "reply": {"type": "TEXT", "text": run["message"]},
            "binding": None,
            "agent": {
                "session_id": run["session_id"],
                "run_id": run["run_id"],
                "state": run["state"],
                "pending_action": pending_type,
            },
            "security": security,
        }

    @staticmethod
    def _error_response(
        gateway_message_id: str,
        code: str,
        message: str,
        retryable: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "gateway_message_id": gateway_message_id,
            "status": "REJECTED",
            "replayed": False,
            "reply": {"type": "TEXT", "text": message},
            "binding": None,
            "agent": None,
            "security": {"mode": "CHANNEL_ONLY", "action": "NONE"},
            "error": {"code": code, "message": message, "retryable": retryable},
        }
