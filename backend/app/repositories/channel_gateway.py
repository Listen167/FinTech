"""Channel Gateway 的事务型持久化仓库。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import (
    ChannelConversationRecord,
    ChannelIdentityRecord,
    ChannelLinkTokenRecord,
    ChannelMessageRecord,
)
from backend.app.errors import ApplicationError


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


class ChannelGatewayRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _identity_payload(record: ChannelIdentityRecord) -> dict[str, Any]:
        return {
            "identity_id": record.identity_id,
            "user_id": record.user_id,
            "channel": record.channel,
            "tenant_id": record.tenant_id,
            "external_user_fingerprint": record.external_user_id_hash[:12],
            "status": record.status,
            "linked_at": _iso_utc(record.linked_at),
            "revoked_at": _iso_utc(record.revoked_at),
            "created_at": _iso_utc(record.created_at),
            "updated_at": _iso_utc(record.updated_at),
        }

    @staticmethod
    def _message_payload(record: ChannelMessageRecord) -> dict[str, Any]:
        return {
            "gateway_message_id": record.gateway_message_id,
            "identity_id": record.identity_id,
            "conversation_id": record.conversation_id,
            "status": record.status,
            "payload_hash": record.payload_hash,
            "run_id": record.run_id,
            "error_code": record.error_code,
            "response_payload": dict(record.response_payload or {}),
        }

    def create_link_token(
        self,
        *,
        user_id: str,
        channel: str,
        token_hash: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        now = _now()
        record = ChannelLinkTokenRecord(
            token_id=f"clt_{uuid4().hex}",
            user_id=user_id,
            channel=channel,
            token_hash=token_hash,
            status="PENDING",
            expires_at=expires_at,
            created_at=now,
        )
        with Session(self.engine) as database, database.begin():
            database.add(record)
            database.flush()
            return {
                "token_id": record.token_id,
                "channel": record.channel,
                "status": record.status,
                "expires_at": _iso_utc(record.expires_at),
                "created_at": _iso_utc(record.created_at),
            }

    def redeem_link_token(
        self,
        *,
        token_hash: str,
        channel: str,
        tenant_id: str,
        external_user_id_hash: str,
        external_user_id_ciphertext: str,
    ) -> dict[str, Any]:
        now = _now()
        expired = False
        result: dict[str, Any] | None = None
        with Session(self.engine) as database, database.begin():
            token = database.scalar(
                select(ChannelLinkTokenRecord)
                .where(ChannelLinkTokenRecord.token_hash == token_hash)
                .with_for_update()
            )
            if token is None or token.channel != channel:
                raise ApplicationError("CHANNEL_LINK_TOKEN_INVALID")
            if token.status == "USED":
                raise ApplicationError("CHANNEL_LINK_TOKEN_REPLAYED")
            if token.status != "PENDING":
                raise ApplicationError("CHANNEL_LINK_TOKEN_INVALID")
            if now > token.expires_at:
                token.status = "EXPIRED"
                expired = True
            else:
                identity = database.scalar(
                    select(ChannelIdentityRecord)
                    .where(
                        ChannelIdentityRecord.channel == channel,
                        ChannelIdentityRecord.tenant_id == tenant_id,
                        ChannelIdentityRecord.external_user_id_hash == external_user_id_hash,
                    )
                    .with_for_update()
                )
                if identity is not None and identity.user_id != token.user_id:
                    raise ApplicationError("CHANNEL_IDENTITY_ALREADY_BOUND")
                if identity is None:
                    identity = ChannelIdentityRecord(
                        identity_id=f"chid_{uuid4().hex}",
                        user_id=token.user_id,
                        channel=channel,
                        tenant_id=tenant_id,
                        external_user_id_hash=external_user_id_hash,
                        external_user_id_ciphertext=external_user_id_ciphertext,
                        status="ACTIVE",
                        linked_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    database.add(identity)
                else:
                    identity.status = "ACTIVE"
                    identity.external_user_id_ciphertext = external_user_id_ciphertext
                    identity.linked_at = now
                    identity.revoked_at = None
                    identity.updated_at = now

                token.status = "USED"
                token.used_at = now
                result = self._identity_payload(identity)
            database.flush()
        if expired:
            raise ApplicationError("CHANNEL_LINK_TOKEN_EXPIRED")
        if result is None:
            raise ApplicationError("CHANNEL_PROCESSING_FAILED")
        return result

    def resolve_identity(
        self,
        *,
        channel: str,
        tenant_id: str,
        external_user_id_hash: str,
    ) -> dict[str, Any] | None:
        with Session(self.engine) as database:
            identity = database.scalar(
                select(ChannelIdentityRecord).where(
                    ChannelIdentityRecord.channel == channel,
                    ChannelIdentityRecord.tenant_id == tenant_id,
                    ChannelIdentityRecord.external_user_id_hash == external_user_id_hash,
                    ChannelIdentityRecord.status == "ACTIVE",
                )
            )
            return self._identity_payload(identity) if identity else None

    def update_delivery_identity_ciphertext(
        self,
        *,
        identity_id: str,
        external_user_id_ciphertext: str,
    ) -> None:
        with Session(self.engine) as database, database.begin():
            identity = database.get(ChannelIdentityRecord, identity_id)
            if identity is None or identity.status != "ACTIVE":
                raise ApplicationError("CHANNEL_IDENTITY_NOT_FOUND")
            identity.external_user_id_ciphertext = external_user_id_ciphertext
            identity.updated_at = _now()

    def get_active_delivery_identity(
        self,
        *,
        user_id: str,
        channel: str = "FEISHU",
    ) -> dict[str, Any] | None:
        """仅供内部 Worker 使用，不得直接作为 API 响应。"""

        with Session(self.engine) as database:
            identity = database.scalar(
                select(ChannelIdentityRecord)
                .where(
                    ChannelIdentityRecord.user_id == user_id,
                    ChannelIdentityRecord.channel == channel,
                    ChannelIdentityRecord.status == "ACTIVE",
                    ChannelIdentityRecord.external_user_id_ciphertext.is_not(None),
                )
                .order_by(ChannelIdentityRecord.updated_at.desc())
            )
            if identity is None:
                return None
            return {
                "identity_id": identity.identity_id,
                "channel": identity.channel,
                "external_user_id_ciphertext": identity.external_user_id_ciphertext,
            }

    def list_identities(self, user_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            records = database.scalars(
                select(ChannelIdentityRecord)
                .where(ChannelIdentityRecord.user_id == user_id)
                .order_by(ChannelIdentityRecord.created_at.desc())
            )
            return [self._identity_payload(record) for record in records]

    def revoke_identity(self, *, user_id: str, identity_id: str) -> dict[str, Any]:
        now = _now()
        with Session(self.engine) as database, database.begin():
            identity = database.get(ChannelIdentityRecord, identity_id)
            if identity is None:
                raise ApplicationError("CHANNEL_IDENTITY_NOT_FOUND")
            if identity.user_id != user_id:
                raise ApplicationError("RESOURCE_FORBIDDEN")
            identity.status = "REVOKED"
            identity.revoked_at = now
            identity.updated_at = now
            database.flush()
            return self._identity_payload(identity)

    def begin_message(
        self,
        *,
        channel: str,
        tenant_id: str,
        external_message_id_hash: str,
        payload_hash: str,
        message_kind: str,
    ) -> tuple[dict[str, Any], bool]:
        now = _now()
        record = ChannelMessageRecord(
            gateway_message_id=f"chmsg_{uuid4().hex}",
            channel=channel,
            tenant_id=tenant_id,
            external_message_id_hash=external_message_id_hash,
            payload_hash=payload_hash,
            message_kind=message_kind,
            status="PROCESSING",
            response_payload={},
            received_at=now,
            updated_at=now,
        )
        try:
            with Session(self.engine) as database, database.begin():
                database.add(record)
                database.flush()
                return self._message_payload(record), True
        except IntegrityError:
            with Session(self.engine) as database:
                existing = database.scalar(
                    select(ChannelMessageRecord).where(
                        ChannelMessageRecord.channel == channel,
                        ChannelMessageRecord.tenant_id == tenant_id,
                        ChannelMessageRecord.external_message_id_hash
                        == external_message_id_hash,
                    )
                )
                if existing is None:
                    raise ApplicationError("CHANNEL_PROCESSING_FAILED")
                if existing.payload_hash != payload_hash:
                    raise ApplicationError("CHANNEL_MESSAGE_CONFLICT")
                return self._message_payload(existing), False

    def complete_message(
        self,
        *,
        gateway_message_id: str,
        status: str,
        response_payload: dict[str, Any],
        identity_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with Session(self.engine) as database, database.begin():
            record = database.get(ChannelMessageRecord, gateway_message_id)
            if record is None:
                raise ApplicationError("CHANNEL_PROCESSING_FAILED")
            record.status = status
            record.response_payload = response_payload
            record.identity_id = identity_id
            record.conversation_id = conversation_id
            record.run_id = run_id
            record.error_code = error_code
            record.processed_at = now
            record.updated_at = now
            database.flush()
            return self._message_payload(record)

    def get_conversation(
        self, *, identity_id: str, external_conversation_id_hash: str
    ) -> dict[str, Any] | None:
        with Session(self.engine) as database:
            record = database.scalar(
                select(ChannelConversationRecord).where(
                    ChannelConversationRecord.identity_id == identity_id,
                    ChannelConversationRecord.external_conversation_id_hash
                    == external_conversation_id_hash,
                    ChannelConversationRecord.status == "ACTIVE",
                )
            )
            if record is None:
                return None
            return {
                "conversation_id": record.conversation_id,
                "identity_id": record.identity_id,
                "agent_session_id": record.agent_session_id,
                "status": record.status,
            }

    def create_conversation(
        self,
        *,
        identity_id: str,
        external_conversation_id_hash: str,
        agent_session_id: str,
    ) -> dict[str, Any]:
        now = _now()
        record = ChannelConversationRecord(
            conversation_id=f"chconv_{uuid4().hex}",
            identity_id=identity_id,
            external_conversation_id_hash=external_conversation_id_hash,
            agent_session_id=agent_session_id,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        try:
            with Session(self.engine) as database, database.begin():
                database.add(record)
                database.flush()
                return {
                    "conversation_id": record.conversation_id,
                    "identity_id": record.identity_id,
                    "agent_session_id": record.agent_session_id,
                    "status": record.status,
                }
        except IntegrityError:
            existing = self.get_conversation(
                identity_id=identity_id,
                external_conversation_id_hash=external_conversation_id_hash,
            )
            if existing is None:
                raise ApplicationError("CHANNEL_PROCESSING_FAILED")
            return existing

    def rebind_conversation(
        self,
        *,
        identity_id: str,
        external_conversation_id_hash: str,
        agent_session_id: str,
    ) -> dict[str, Any]:
        now = _now()
        with Session(self.engine) as database, database.begin():
            record = database.scalar(
                select(ChannelConversationRecord)
                .where(
                    ChannelConversationRecord.identity_id == identity_id,
                    ChannelConversationRecord.external_conversation_id_hash
                    == external_conversation_id_hash,
                    ChannelConversationRecord.status == "ACTIVE",
                )
                .with_for_update()
            )
            if record is None:
                raise ApplicationError("CHANNEL_PROCESSING_FAILED")
            record.agent_session_id = agent_session_id
            record.updated_at = now
            database.flush()
            return {
                "conversation_id": record.conversation_id,
                "identity_id": record.identity_id,
                "agent_session_id": record.agent_session_id,
                "status": record.status,
            }
