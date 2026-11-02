"""家庭守护授权圈：一次性邀请、最小 Consent 和风险双确认。"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import Engine, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AgentRunRecord,
    GuardianConsentRecord,
    GuardianInvitationRecord,
    GuardianRiskReviewRecord,
    UserRecord,
    utc_now,
)
from backend.app.errors import ApplicationError
from backend.app.repositories.guardian_circle import GuardianCircleRepository
from backend.app.schemas.guardian_circle import (
    CreateGuardianInvitationRequest,
    CreateGuardianRiskReviewRequest,
)


PENDING_REVIEW_STATES = {"AWAITING_OWNER", "AWAITING_GUARDIAN"}


class GuardianCircleError(ApplicationError):
    pass


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _pair_key(owner_user_id: str, guardian_user_id: str) -> str:
    return f"{owner_user_id}:{guardian_user_id}"


def _amount_band(amount: Decimal) -> str:
    if amount < Decimal("1000"):
        return "1000 元以下"
    if amount < Decimal("5000"):
        return "1000 至 4999 元"
    if amount < Decimal("50000"):
        return "5000 至 49999 元"
    return "50000 元及以上"


def _sanitize_summary(value: str) -> str:
    collapsed = " ".join(value.split())
    return re.sub(r"(?<!\d)\d{4,}(?!\d)", "****", collapsed)


class GuardianCircleService:
    def __init__(
        self,
        engine: Engine,
        *,
        invite_ttl_seconds: int = 600,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not 60 <= invite_ttl_seconds <= 86400:
            raise ValueError("GUARDIAN_INVITE_TTL_OUT_OF_RANGE")
        self.engine = engine
        self.repository = GuardianCircleRepository(engine)
        self.invite_ttl_seconds = invite_ttl_seconds
        self.clock = clock

    def _now(self, supplied: datetime | None = None) -> datetime:
        return _utc_naive(supplied or self.clock())

    def _expire_due(self, database: Session, now: datetime) -> None:
        invitations = database.scalars(
            select(GuardianInvitationRecord).where(
                GuardianInvitationRecord.status == "PENDING",
                GuardianInvitationRecord.expires_at <= now,
            ).with_for_update()
        ).all()
        for row in invitations:
            row.status = "EXPIRED"
            row.responded_at = now
            row.updated_at = now
            self.repository.add_audit(
                database,
                owner_user_id=row.owner_user_id,
                guardian_user_id=row.guardian_user_id,
                actor_user_id=None,
                event_type="INVITATION_EXPIRED",
                result="EXPIRED",
                invitation_id=row.invitation_id,
                now=now,
            )

        consents = database.scalars(
            select(GuardianConsentRecord).where(
                GuardianConsentRecord.status == "ACTIVE",
                GuardianConsentRecord.valid_until <= now,
            ).with_for_update()
        ).all()
        for row in consents:
            row.status = "EXPIRED"
            row.active_pair_key = None
            row.updated_at = now
            self._cancel_pending_reviews(database, row.consent_id, now)
            self.repository.add_audit(
                database,
                owner_user_id=row.owner_user_id,
                guardian_user_id=row.guardian_user_id,
                actor_user_id=None,
                event_type="CONSENT_EXPIRED",
                result="EXPIRED",
                consent_id=row.consent_id,
                invitation_id=row.invitation_id,
                now=now,
            )

        reviews = database.scalars(
            select(GuardianRiskReviewRecord).where(
                GuardianRiskReviewRecord.status.in_(PENDING_REVIEW_STATES),
                GuardianRiskReviewRecord.expires_at <= now,
            ).with_for_update()
        ).all()
        for row in reviews:
            row.status = "EXPIRED"
            row.updated_at = now
            self.repository.add_audit(
                database,
                owner_user_id=row.owner_user_id,
                guardian_user_id=row.guardian_user_id,
                actor_user_id=None,
                event_type="RISK_REVIEW_EXPIRED",
                result="EXPIRED",
                consent_id=row.consent_id,
                review_id=row.review_id,
                now=now,
            )

    def _persist_expirations(self, now: datetime) -> None:
        with Session(self.engine) as database, database.begin():
            self._expire_due(database, now)

    @staticmethod
    def _cancel_pending_reviews(database: Session, consent_id: str, now: datetime) -> int:
        rows = database.scalars(
            select(GuardianRiskReviewRecord).where(
                GuardianRiskReviewRecord.consent_id == consent_id,
                GuardianRiskReviewRecord.status.in_(PENDING_REVIEW_STATES),
            ).with_for_update()
        ).all()
        for row in rows:
            row.status = "CANCELED"
            row.updated_at = now
        return len(rows)

    @staticmethod
    def _names(database: Session, owner_user_id: str, guardian_user_id: str) -> dict[str, str]:
        return GuardianCircleRepository.display_names(
            database, {owner_user_id, guardian_user_id}
        )

    def list_candidates(self, user_id: str) -> list[dict[str, str | None]]:
        return self.repository.list_candidates(user_id)

    def create_invitation(
        self,
        *,
        owner_user_id: str,
        payload: CreateGuardianInvitationRequest,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        valid_until = _utc_naive(payload.consent_valid_until)
        if payload.guardian_user_id == owner_user_id:
            raise GuardianCircleError("GUARDIAN_SELF_INVITATION_FORBIDDEN")
        if valid_until <= current + timedelta(minutes=10):
            raise GuardianCircleError("GUARDIAN_CONSENT_VALIDITY_INVALID")
        if valid_until > current + timedelta(days=366):
            raise GuardianCircleError("GUARDIAN_CONSENT_VALIDITY_INVALID")

        self._persist_expirations(current)
        raw_token = secrets.token_urlsafe(32)
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            self._expire_due(database, current)
            guardian = database.get(UserRecord, payload.guardian_user_id)
            if guardian is None or guardian.status != "ACTIVE":
                raise GuardianCircleError("GUARDIAN_USER_NOT_FOUND")
            active = database.scalar(select(GuardianConsentRecord.consent_id).where(
                GuardianConsentRecord.active_pair_key == _pair_key(
                    owner_user_id, payload.guardian_user_id
                )
            ))
            if active is not None:
                raise GuardianCircleError("GUARDIAN_CONSENT_ALREADY_ACTIVE")
            pending = database.scalar(select(GuardianInvitationRecord.invitation_id).where(
                GuardianInvitationRecord.owner_user_id == owner_user_id,
                GuardianInvitationRecord.guardian_user_id == payload.guardian_user_id,
                GuardianInvitationRecord.status == "PENDING",
            ))
            if pending is not None:
                raise GuardianCircleError("GUARDIAN_INVITATION_ALREADY_PENDING")

            row = GuardianInvitationRecord(
                invitation_id=f"guardian_invite_{uuid4().hex[:16]}",
                owner_user_id=owner_user_id,
                guardian_user_id=payload.guardian_user_id,
                token_hash=_token_hash(raw_token),
                status="PENDING",
                scenarios=sorted(set(payload.scenarios)),
                amount_lower_bound=payload.amount_lower_bound,
                amount_upper_bound=payload.amount_upper_bound,
                visible_fields=sorted(set(payload.visible_fields)),
                consent_valid_until=valid_until,
                expires_at=min(
                    current + timedelta(seconds=self.invite_ttl_seconds), valid_until
                ),
                responded_at=None,
                created_at=current,
                updated_at=current,
            )
            database.add(row)
            self.repository.add_audit(
                database,
                owner_user_id=owner_user_id,
                guardian_user_id=payload.guardian_user_id,
                actor_user_id=owner_user_id,
                event_type="INVITATION_CREATED",
                result="PENDING",
                invitation_id=row.invitation_id,
                details={
                    "scenarios": list(row.scenarios),
                    "visible_fields": list(row.visible_fields),
                    "valid_until": valid_until.isoformat(),
                },
                now=current,
            )
            database.flush()
            names = self._names(database, owner_user_id, payload.guardian_user_id)
            return self.repository.invitation_to_dict(
                row, names, invite_token=raw_token
            )

    def list_invitations(self, user_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as database, database.begin():
            self._expire_due(database, self._now(now))
        return self.repository.list_invitations(user_id)

    def _find_invitation_by_token(
        self,
        database: Session,
        token: str,
    ) -> GuardianInvitationRecord:
        row = database.scalar(select(GuardianInvitationRecord).where(
            GuardianInvitationRecord.token_hash == _token_hash(token)
        ).with_for_update())
        if row is None:
            raise GuardianCircleError("GUARDIAN_INVITATION_INVALID")
        return row

    @staticmethod
    def _assert_pending_invitation(
        row: GuardianInvitationRecord,
        guardian_user_id: str,
    ) -> None:
        if row.guardian_user_id != guardian_user_id:
            raise GuardianCircleError("RESOURCE_FORBIDDEN")
        if row.status == "EXPIRED":
            raise GuardianCircleError("GUARDIAN_INVITATION_EXPIRED")
        if row.status != "PENDING":
            raise GuardianCircleError("GUARDIAN_INVITATION_REPLAYED")

    def accept_invitation(
        self,
        *,
        guardian_user_id: str,
        token: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._persist_expirations(current)
        try:
            with Session(self.engine, expire_on_commit=False) as database, database.begin():
                self._expire_due(database, current)
                invitation = self._find_invitation_by_token(database, token)
                self._assert_pending_invitation(invitation, guardian_user_id)
                if invitation.consent_valid_until <= current:
                    invitation.status = "EXPIRED"
                    invitation.responded_at = current
                    invitation.updated_at = current
                    raise GuardianCircleError("GUARDIAN_INVITATION_EXPIRED")
                active = database.scalar(select(GuardianConsentRecord.consent_id).where(
                    GuardianConsentRecord.active_pair_key == _pair_key(
                        invitation.owner_user_id, guardian_user_id
                    )
                ))
                if active is not None:
                    raise GuardianCircleError("GUARDIAN_CONSENT_ALREADY_ACTIVE")

                invitation.status = "ACCEPTED"
                invitation.responded_at = current
                invitation.updated_at = current
                consent = GuardianConsentRecord(
                    consent_id=f"guardian_consent_{uuid4().hex[:16]}",
                    invitation_id=invitation.invitation_id,
                    owner_user_id=invitation.owner_user_id,
                    guardian_user_id=guardian_user_id,
                    active_pair_key=_pair_key(invitation.owner_user_id, guardian_user_id),
                    status="ACTIVE",
                    scenarios=list(invitation.scenarios),
                    amount_lower_bound=invitation.amount_lower_bound,
                    amount_upper_bound=invitation.amount_upper_bound,
                    visible_fields=list(invitation.visible_fields),
                    activated_at=current,
                    valid_until=invitation.consent_valid_until,
                    revoked_at=None,
                    revoked_by_user_id=None,
                    revoke_reason=None,
                    created_at=current,
                    updated_at=current,
                )
                database.add(consent)
                self.repository.add_audit(
                    database,
                    owner_user_id=invitation.owner_user_id,
                    guardian_user_id=guardian_user_id,
                    actor_user_id=guardian_user_id,
                    event_type="INVITATION_ACCEPTED",
                    result="ACTIVE",
                    invitation_id=invitation.invitation_id,
                    consent_id=consent.consent_id,
                    details={"risk_acknowledgement_only": True},
                    now=current,
                )
                database.flush()
                names = self._names(
                    database, invitation.owner_user_id, guardian_user_id
                )
                return {
                    "invitation": self.repository.invitation_to_dict(
                        invitation, names
                    ),
                    "consent": self.repository.consent_to_dict(
                        consent, names, guardian_user_id
                    ),
                    "funds_execution_allowed": False,
                }
        except IntegrityError as error:
            raise GuardianCircleError("GUARDIAN_CONSENT_ALREADY_ACTIVE") from error

    def reject_invitation(
        self,
        *,
        guardian_user_id: str,
        token: str,
        reason: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._persist_expirations(current)
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            self._expire_due(database, current)
            row = self._find_invitation_by_token(database, token)
            self._assert_pending_invitation(row, guardian_user_id)
            row.status = "REJECTED"
            row.responded_at = current
            row.updated_at = current
            self.repository.add_audit(
                database,
                owner_user_id=row.owner_user_id,
                guardian_user_id=guardian_user_id,
                actor_user_id=guardian_user_id,
                event_type="INVITATION_REJECTED",
                result="REJECTED",
                invitation_id=row.invitation_id,
                details={"reason": reason or "未提供原因"},
                now=current,
            )
            names = self._names(database, row.owner_user_id, guardian_user_id)
            return self.repository.invitation_to_dict(row, names)

    def cancel_invitation(
        self,
        *,
        owner_user_id: str,
        invitation_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._persist_expirations(current)
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            self._expire_due(database, current)
            row = database.scalar(select(GuardianInvitationRecord).where(
                GuardianInvitationRecord.invitation_id == invitation_id
            ).with_for_update())
            if row is None:
                raise GuardianCircleError("GUARDIAN_INVITATION_NOT_FOUND")
            if row.owner_user_id != owner_user_id:
                raise GuardianCircleError("RESOURCE_FORBIDDEN")
            if row.status != "PENDING":
                raise GuardianCircleError("GUARDIAN_INVITATION_NOT_PENDING")
            row.status = "CANCELED"
            row.responded_at = current
            row.updated_at = current
            self.repository.add_audit(
                database,
                owner_user_id=owner_user_id,
                guardian_user_id=row.guardian_user_id,
                actor_user_id=owner_user_id,
                event_type="INVITATION_CANCELED",
                result="CANCELED",
                invitation_id=row.invitation_id,
                details={"reason": reason},
                now=current,
            )
            names = self._names(database, owner_user_id, row.guardian_user_id)
            return self.repository.invitation_to_dict(row, names)

    def list_consents(self, user_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as database, database.begin():
            self._expire_due(database, self._now(now))
        return self.repository.list_consents(user_id)

    def _load_consent(
        self,
        database: Session,
        consent_id: str,
        actor_user_id: str,
        *,
        require_active: bool,
    ) -> GuardianConsentRecord:
        row = database.scalar(select(GuardianConsentRecord).where(
            GuardianConsentRecord.consent_id == consent_id
        ).with_for_update())
        if row is None:
            raise GuardianCircleError("GUARDIAN_CONSENT_NOT_FOUND")
        if actor_user_id not in {row.owner_user_id, row.guardian_user_id}:
            raise GuardianCircleError("RESOURCE_FORBIDDEN")
        if require_active and row.status != "ACTIVE":
            raise GuardianCircleError("GUARDIAN_CONSENT_NOT_ACTIVE")
        return row

    def revoke_consent(
        self,
        *,
        actor_user_id: str,
        consent_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._persist_expirations(current)
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            self._expire_due(database, current)
            row = self._load_consent(
                database, consent_id, actor_user_id, require_active=True
            )
            row.status = "REVOKED"
            row.active_pair_key = None
            row.revoked_at = current
            row.revoked_by_user_id = actor_user_id
            row.revoke_reason = reason
            row.updated_at = current
            canceled = self._cancel_pending_reviews(database, consent_id, current)
            self.repository.add_audit(
                database,
                owner_user_id=row.owner_user_id,
                guardian_user_id=row.guardian_user_id,
                actor_user_id=actor_user_id,
                event_type="CONSENT_REVOKED",
                result="REVOKED",
                invitation_id=row.invitation_id,
                consent_id=row.consent_id,
                details={"reason": reason, "canceled_reviews": canceled},
                now=current,
            )
            names = self._names(database, row.owner_user_id, row.guardian_user_id)
            return self.repository.consent_to_dict(row, names, actor_user_id)

    @staticmethod
    def _validate_masked_recipient(value: str | None) -> None:
        if value is not None and re.search(r"\d{6,}", value):
            raise GuardianCircleError("GUARDIAN_SENSITIVE_FIELD_FORBIDDEN")

    @staticmethod
    def _visible_payload(
        consent: GuardianConsentRecord,
        payload: CreateGuardianRiskReviewRequest,
        now: datetime,
    ) -> dict[str, Any]:
        allowed = set(consent.visible_fields)
        values: dict[str, Any] = {}
        if "RISK_LEVEL" in allowed:
            values["risk_level"] = payload.risk_level
        if "SCENARIO" in allowed:
            values["scenario"] = payload.scenario
        if "AMOUNT_BAND" in allowed:
            values["amount_band"] = _amount_band(payload.amount)
        if "RECIPIENT_MASKED" in allowed:
            values["recipient_masked"] = payload.recipient_masked or "未提供"
        if "RISK_SUMMARY" in allowed:
            values["risk_summary"] = _sanitize_summary(payload.risk_summary)
        if "OCCURRED_AT" in allowed:
            values["occurred_at"] = now.isoformat()
        return values

    def create_risk_review(
        self,
        *,
        owner_user_id: str,
        payload: CreateGuardianRiskReviewRequest,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._validate_masked_recipient(payload.recipient_masked)
        self._persist_expirations(current)
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            self._expire_due(database, current)
            consent = self._load_consent(
                database, payload.consent_id, owner_user_id, require_active=True
            )
            if consent.owner_user_id != owner_user_id:
                raise GuardianCircleError("RESOURCE_FORBIDDEN")
            if payload.scenario not in consent.scenarios:
                raise GuardianCircleError("GUARDIAN_SCOPE_VIOLATION")
            if not consent.amount_lower_bound <= payload.amount <= consent.amount_upper_bound:
                raise GuardianCircleError("GUARDIAN_SCOPE_VIOLATION")
            if payload.source_run_id is not None:
                run = database.get(AgentRunRecord, payload.source_run_id)
                if run is None or run.user_id != owner_user_id:
                    raise GuardianCircleError("SOURCE_RUN_NOT_FOUND")

            row = GuardianRiskReviewRecord(
                review_id=f"guardian_review_{uuid4().hex[:16]}",
                consent_id=consent.consent_id,
                owner_user_id=owner_user_id,
                guardian_user_id=consent.guardian_user_id,
                source_run_id=payload.source_run_id,
                scenario=payload.scenario,
                amount=payload.amount,
                risk_level=payload.risk_level,
                recipient_masked=payload.recipient_masked,
                risk_summary=_sanitize_summary(payload.risk_summary),
                visible_payload=self._visible_payload(consent, payload, current),
                status="AWAITING_OWNER",
                owner_note=None,
                guardian_note=None,
                owner_decided_at=None,
                guardian_decided_at=None,
                expires_at=min(
                    current + timedelta(seconds=payload.expires_in_seconds),
                    consent.valid_until,
                ),
                created_at=current,
                updated_at=current,
            )
            database.add(row)
            self.repository.add_audit(
                database,
                owner_user_id=owner_user_id,
                guardian_user_id=consent.guardian_user_id,
                actor_user_id=owner_user_id,
                event_type="RISK_REVIEW_CREATED",
                result="AWAITING_OWNER",
                consent_id=consent.consent_id,
                review_id=row.review_id,
                details={
                    "scenario": row.scenario,
                    "visible_fields": sorted(row.visible_payload),
                },
                now=current,
            )
            database.flush()
            names = self._names(database, owner_user_id, consent.guardian_user_id)
            return self.repository.review_to_dict(row, names, owner_user_id)

    def _load_review(
        self,
        database: Session,
        review_id: str,
        actor_user_id: str,
    ) -> tuple[GuardianRiskReviewRecord, GuardianConsentRecord]:
        row = database.scalar(select(GuardianRiskReviewRecord).where(
            GuardianRiskReviewRecord.review_id == review_id
        ).with_for_update())
        if row is None:
            raise GuardianCircleError("GUARDIAN_RISK_REVIEW_NOT_FOUND")
        if actor_user_id not in {row.owner_user_id, row.guardian_user_id}:
            raise GuardianCircleError("RESOURCE_FORBIDDEN")
        consent = database.get(GuardianConsentRecord, row.consent_id)
        if consent is None:
            raise GuardianCircleError("GUARDIAN_CONSENT_NOT_FOUND")
        if actor_user_id == row.guardian_user_id and consent.status != "ACTIVE":
            raise GuardianCircleError("GUARDIAN_CONSENT_NOT_ACTIVE")
        return row, consent

    def list_risk_reviews(
        self, user_id: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as database, database.begin():
            self._expire_due(database, self._now(now))
        return self.repository.list_reviews(user_id)

    def get_risk_review(
        self,
        *,
        actor_user_id: str,
        review_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._persist_expirations(current)
        with Session(self.engine) as database, database.begin():
            self._expire_due(database, current)
            row, _ = self._load_review(database, review_id, actor_user_id)
            names = self._names(database, row.owner_user_id, row.guardian_user_id)
            return self.repository.review_to_dict(row, names, actor_user_id)

    def owner_decide(
        self,
        *,
        owner_user_id: str,
        review_id: str,
        confirmed: bool,
        note: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._persist_expirations(current)
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            self._expire_due(database, current)
            row, consent = self._load_review(database, review_id, owner_user_id)
            if row.owner_user_id != owner_user_id:
                raise GuardianCircleError("RESOURCE_FORBIDDEN")
            if consent.status != "ACTIVE":
                raise GuardianCircleError("GUARDIAN_CONSENT_NOT_ACTIVE")
            if row.status != "AWAITING_OWNER":
                raise GuardianCircleError("GUARDIAN_RISK_REVIEW_NOT_OWNER_CONFIRMABLE")
            row.status = "AWAITING_GUARDIAN" if confirmed else "REJECTED"
            row.owner_note = note
            row.owner_decided_at = current
            row.updated_at = current
            self.repository.add_audit(
                database,
                owner_user_id=row.owner_user_id,
                guardian_user_id=row.guardian_user_id,
                actor_user_id=owner_user_id,
                event_type="OWNER_RISK_DECISION",
                result=row.status,
                consent_id=row.consent_id,
                review_id=row.review_id,
                details={"confirmed": confirmed},
                now=current,
            )
            names = self._names(database, row.owner_user_id, row.guardian_user_id)
            return self.repository.review_to_dict(row, names, owner_user_id)

    def guardian_decide(
        self,
        *,
        guardian_user_id: str,
        review_id: str,
        risk_acknowledged: bool,
        note: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = self._now(now)
        self._persist_expirations(current)
        with Session(self.engine, expire_on_commit=False) as database, database.begin():
            self._expire_due(database, current)
            row, consent = self._load_review(database, review_id, guardian_user_id)
            if row.guardian_user_id != guardian_user_id:
                raise GuardianCircleError("RESOURCE_FORBIDDEN")
            if consent.status != "ACTIVE":
                raise GuardianCircleError("GUARDIAN_CONSENT_NOT_ACTIVE")
            if row.status != "AWAITING_GUARDIAN":
                raise GuardianCircleError("GUARDIAN_RISK_REVIEW_NOT_GUARDIAN_CONFIRMABLE")
            row.status = "ACKNOWLEDGED" if risk_acknowledged else "REJECTED"
            row.guardian_note = note
            row.guardian_decided_at = current
            row.updated_at = current
            self.repository.add_audit(
                database,
                owner_user_id=row.owner_user_id,
                guardian_user_id=row.guardian_user_id,
                actor_user_id=guardian_user_id,
                event_type="GUARDIAN_RISK_DECISION",
                result=row.status,
                consent_id=row.consent_id,
                review_id=row.review_id,
                details={"risk_acknowledged": risk_acknowledged},
                now=current,
            )
            names = self._names(database, row.owner_user_id, row.guardian_user_id)
            return self.repository.review_to_dict(row, names, guardian_user_id)

    def audits(
        self,
        *,
        actor_user_id: str,
        consent_id: str,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        current = self._now(now)
        self._persist_expirations(current)
        with Session(self.engine) as database, database.begin():
            self._expire_due(database, current)
            row = self._load_consent(
                database, consent_id, actor_user_id, require_active=False
            )
            if actor_user_id == row.guardian_user_id and row.status != "ACTIVE":
                raise GuardianCircleError("GUARDIAN_CONSENT_NOT_ACTIVE")
            invitation_id = row.invitation_id
        return self.repository.list_audits(consent_id, invitation_id)

    def reset_user(self, user_id: str) -> None:
        self.repository.reset_user(user_id)
