"""飞书 Webhook 验签、解密、时间窗校验与统一消息投递。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
from datetime import datetime, timezone
from typing import Any, Mapping

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from backend.app.errors import ApplicationError
from backend.app.integrations.feishu_adapter import (
    FeishuEventError,
    FeishuReplier,
    deliver_parsed_event,
    parse_feishu_event,
)
from backend.app.schemas.channel import ChannelWorkerHeartbeat
from backend.app.services.channel_gateway import ChannelGatewayService


class FeishuWebhookVerifier:
    def __init__(
        self,
        *,
        verification_token: str,
        encrypt_key: str,
        max_skew_seconds: int = 300,
    ) -> None:
        if not verification_token or not encrypt_key:
            raise ValueError("FEISHU_WEBHOOK_CONFIG_REQUIRED")
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.max_skew_seconds = max_skew_seconds

    def verify_and_decode(
        self,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not raw_body or len(raw_body) > 1_000_000:
            raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID")
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        timestamp = normalized_headers.get("x-lark-request-timestamp", "")
        nonce = normalized_headers.get("x-lark-request-nonce", "")
        signature = normalized_headers.get("x-lark-signature", "")
        try:
            timestamp_value = int(timestamp)
        except ValueError as error:
            raise ApplicationError("FEISHU_WEBHOOK_TIMESTAMP_INVALID") from error
        current = now or datetime.now(timezone.utc)
        if abs(int(current.timestamp()) - timestamp_value) > self.max_skew_seconds:
            raise ApplicationError("FEISHU_WEBHOOK_TIMESTAMP_INVALID")
        expected = hashlib.sha256(
            timestamp.encode("utf-8")
            + nonce.encode("utf-8")
            + self.encrypt_key.encode("utf-8")
            + raw_body
        ).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            raise ApplicationError("FEISHU_WEBHOOK_SIGNATURE_INVALID")

        try:
            envelope = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID") from error
        if not isinstance(envelope, dict):
            raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID")
        payload = self._decrypt(envelope["encrypt"]) if "encrypt" in envelope else envelope
        header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
        token = payload.get("token") or header.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(
            token,
            self.verification_token,
        ):
            raise ApplicationError("FEISHU_WEBHOOK_TOKEN_INVALID")
        return payload

    def _decrypt(self, encrypted_value: Any) -> dict[str, Any]:
        if not isinstance(encrypted_value, str):
            raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID")
        try:
            encrypted = base64.b64decode(encrypted_value, validate=True)
            if len(encrypted) < 32 or len(encrypted) % 16:
                raise ValueError("invalid ciphertext length")
            key = hashlib.sha256(self.encrypt_key.encode("utf-8")).digest()
            decryptor = Cipher(
                algorithms.AES(key),
                modes.CBC(encrypted[:16]),
            ).decryptor()
            padded = decryptor.update(encrypted[16:]) + decryptor.finalize()
            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as error:
            raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID") from error
        if not isinstance(payload, dict):
            raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID")
        return payload


class FeishuWebhookHandler:
    def __init__(
        self,
        *,
        verifier: FeishuWebhookVerifier,
        gateway: ChannelGatewayService,
        replier: FeishuReplier,
        worker_lease_seconds: int,
        worker_id: str | None = None,
    ) -> None:
        self.verifier = verifier
        self.gateway = gateway
        self.replier = replier
        self.worker_lease_seconds = worker_lease_seconds
        self.worker_id = worker_id or (
            f"webhook:{socket.gethostname()}:{os.getpid()}"
        )

    def handle(self, *, headers: Mapping[str, str], raw_body: bytes) -> dict[str, Any]:
        payload = self.verifier.verify_and_decode(headers=headers, raw_body=raw_body)
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge or len(challenge) > 512:
                raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID")
            return {"challenge": challenge}

        header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
        if header.get("event_type") != "im.message.receive_v1":
            return {"code": 0, "message": "ignored"}
        try:
            parsed = parse_feishu_event(payload)
        except FeishuEventError as error:
            raise ApplicationError("FEISHU_WEBHOOK_PAYLOAD_INVALID") from error

        self.gateway.record_worker_heartbeat(
            ChannelWorkerHeartbeat(
                worker_id=self.worker_id,
                worker_kind="FEISHU_INBOUND",
                transport="WEBHOOK",
                state="ONLINE",
                lease_seconds=self.worker_lease_seconds,
            ),
            default_lease_seconds=self.worker_lease_seconds,
        )
        deliver_parsed_event(self.gateway, self.replier, parsed)
        return {"code": 0, "message": "success"}
