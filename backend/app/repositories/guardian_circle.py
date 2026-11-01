"""家庭守护圈 SQL 仓库与脱敏序列化。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, and_, delete, or_, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    GuardianAuditRecord,
    GuardianConsentRecord,
    GuardianInvitationRecord,
    GuardianRiskReviewRecord,
    UserRecord,
)


def _money(value: Decimal) -> str:
    return format(value, ".2f")


class GuardianCircleRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def display_names(database: Session, user_ids: set[str]) -> dict[str, str]:
        if not user_ids:
            return {}
        rows = database.execute(
            select(UserRecord.id, UserRecord.display_name).where(UserRecord.id.in_(user_ids))
        ).all()
        return {user_id: display_name for user_id, display_name in rows}

    @staticmethod
    def invitation_to_dict(
        row: GuardianInvitationRecord,
        names: dict[str, str],
        *,
        invite_token: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "invitation_id": row.invitation_id,
            "status": row.status,
            "owner": {
                "user_id": row.owner_user_id,
                "display_name": names.get(row.owner_user_id, "已停用用户"),
            },
            "guardian": {
                "user_id": row.guardian_user_id,
                "display_name": names.get(row.guardian_user_id, "已停用用户"),
            },
            "scope": {
                "scenarios": list(row.scenarios),
                "amount_lower_bound": _money(row.amount_lower_bound),
                "amount_upper_bound": _money(row.amount_upper_bound),
                "visible_fields": list(row.visible_fields),
                "valid_until": row.consent_valid_until.isoformat(),
            },
            "expires_at": row.expires_at.isoformat(),
            "responded_at": row.responded_at.isoformat() if row.responded_at else None,
            "created_at": row.created_at.isoformat(),
        }
        if invite_token is not None:
            result["invite_token"] = invite_token
            result["token_returned_once"] = True
        return result

    @staticmethod
    def consent_to_dict(
        row: GuardianConsentRecord,
        names: dict[str, str],
        viewer_user_id: str,
    ) -> dict[str, Any]:
        return {
            "consent_id": row.consent_id,
            "status": row.status,
            "viewer_role": "OWNER" if viewer_user_id == row.owner_user_id else "GUARDIAN",
            "owner": {
                "user_id": row.owner_user_id,
                "display_name": names.get(row.owner_user_id, "已停用用户"),
            },
            "guardian": {
                "user_id": row.guardian_user_id,
                "display_name": names.get(row.guardian_user_id, "已停用用户"),
            },
            "scope": {
                "scenarios": list(row.scenarios),
                "amount_lower_bound": _money(row.amount_lower_bound),
                "amount_upper_bound": _money(row.amount_upper_bound),
                "visible_fields": list(row.visible_fields),
                "valid_until": row.valid_until.isoformat(),
            },
            "activated_at": row.activated_at.isoformat(),
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "revoked_by_user_id": row.revoked_by_user_id,
            "revoke_reason": row.revoke_reason,
            "capabilities": {
                "risk_summary_only": True,
                "can_acknowledge_risk": row.status == "ACTIVE",
                "can_execute_funds": False,
                "can_satisfy_mfa": False,
            },
        }

    @staticmethod
    def review_to_dict(
        row: GuardianRiskReviewRecord,
        names: dict[str, str],
        viewer_user_id: str,
    ) -> dict[str, Any]:
        return {
            "review_id": row.review_id,
            "consent_id": row.consent_id,
            "status": row.status,
            "viewer_role": "OWNER" if viewer_user_id == row.owner_user_id else "GUARDIAN",
            "owner": {
                "user_id": row.owner_user_id,
                "display_name": names.get(row.owner_user_id, "已停用用户"),
            },
            "guardian": {
                "user_id": row.guardian_user_id,
                "display_name": names.get(row.guardian_user_id, "已停用用户"),
            },
            "visible_payload": deepcopy(row.visible_payload),
            "source_run_id": row.source_run_id if viewer_user_id == row.owner_user_id else None,
            "owner_note": row.owner_note if viewer_user_id == row.owner_user_id else None,
            "guardian_note": row.guardian_note,
            "owner_decided_at": (
                row.owner_decided_at.isoformat() if row.owner_decided_at else None
            ),
            "guardian_decided_at": (
                row.guardian_decided_at.isoformat() if row.guardian_decided_at else None
            ),
            "expires_at": row.expires_at.isoformat(),
            "created_at": row.created_at.isoformat(),
            "safety_boundary": {
                "risk_acknowledgement_only": True,
                "funds_executed": False,
                "mfa_satisfied": False,
                "execution_authorized": False,
            },
        }

    @staticmethod
    def audit_to_dict(row: GuardianAuditRecord) -> dict[str, Any]:
        return {
            "audit_id": row.audit_id,
            "consent_id": row.consent_id,
            "invitation_id": row.invitation_id,
            "review_id": row.review_id,
            "actor_user_id": row.actor_user_id,
            "event_type": row.event_type,
            "result": row.result,
            "details": deepcopy(row.details),
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def add_audit(
        database: Session,
        *,
        owner_user_id: str,
        guardian_user_id: str,
        actor_user_id: str | None,
        event_type: str,
        result: str,
        now: datetime,
        consent_id: str | None = None,
        invitation_id: str | None = None,
        review_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> GuardianAuditRecord:
        row = GuardianAuditRecord(
            audit_id=f"guardian_audit_{uuid4().hex[:16]}",
            consent_id=consent_id,
            invitation_id=invitation_id,
            review_id=review_id,
            owner_user_id=owner_user_id,
            guardian_user_id=guardian_user_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            result=result,
            details=deepcopy(details or {}),
            created_at=now,
        )
        database.add(row)
        return row

    def list_invitations(self, user_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            rows = database.scalars(
                select(GuardianInvitationRecord)
                .where(or_(
                    GuardianInvitationRecord.owner_user_id == user_id,
                    GuardianInvitationRecord.guardian_user_id == user_id,
                ))
                .order_by(GuardianInvitationRecord.created_at.desc())
            ).all()
            names = self.display_names(
                database,
                {item for row in rows for item in (row.owner_user_id, row.guardian_user_id)},
            )
            return [self.invitation_to_dict(row, names) for row in rows]

    def list_consents(self, user_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            rows = database.scalars(
                select(GuardianConsentRecord)
                .where(or_(
                    GuardianConsentRecord.owner_user_id == user_id,
                    and_(
                        GuardianConsentRecord.guardian_user_id == user_id,
                        GuardianConsentRecord.status == "ACTIVE",
                    ),
                ))
                .order_by(GuardianConsentRecord.created_at.desc())
            ).all()
            names = self.display_names(
                database,
                {item for row in rows for item in (row.owner_user_id, row.guardian_user_id)},
            )
            return [self.consent_to_dict(row, names, user_id) for row in rows]

    def list_reviews(self, user_id: str, *, guardian_active_only: bool = True) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            query = (
                select(GuardianRiskReviewRecord, GuardianConsentRecord)
                .join(
                    GuardianConsentRecord,
                    GuardianConsentRecord.consent_id == GuardianRiskReviewRecord.consent_id,
                )
                .where(or_(
                    GuardianRiskReviewRecord.owner_user_id == user_id,
                    GuardianRiskReviewRecord.guardian_user_id == user_id,
                ))
                .order_by(GuardianRiskReviewRecord.created_at.desc())
            )
            pairs = database.execute(query).all()
            if guardian_active_only:
                pairs = [
                    (review, consent)
                    for review, consent in pairs
                    if review.owner_user_id == user_id or consent.status == "ACTIVE"
                ]
            reviews = [review for review, _ in pairs]
            names = self.display_names(
                database,
                {item for row in reviews for item in (row.owner_user_id, row.guardian_user_id)},
            )
            return [self.review_to_dict(row, names, user_id) for row in reviews]

    def list_audits(self, consent_id: str, invitation_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as database:
            rows = database.scalars(
                select(GuardianAuditRecord)
                .where(or_(
                    GuardianAuditRecord.consent_id == consent_id,
                    GuardianAuditRecord.invitation_id == invitation_id,
                ))
                .order_by(GuardianAuditRecord.created_at, GuardianAuditRecord.audit_id)
            ).all()
            return [self.audit_to_dict(row) for row in rows]

    def reset_user(self, user_id: str) -> None:
        with Session(self.engine) as database, database.begin():
            participant = or_(
                GuardianInvitationRecord.owner_user_id == user_id,
                GuardianInvitationRecord.guardian_user_id == user_id,
            )
            invitations = database.scalars(
                select(GuardianInvitationRecord.invitation_id).where(participant)
            ).all()
            consents = database.scalars(
                select(GuardianConsentRecord.consent_id).where(or_(
                    GuardianConsentRecord.owner_user_id == user_id,
                    GuardianConsentRecord.guardian_user_id == user_id,
                ))
            ).all()
            database.execute(delete(GuardianAuditRecord).where(or_(
                GuardianAuditRecord.owner_user_id == user_id,
                GuardianAuditRecord.guardian_user_id == user_id,
            )))
            if consents:
                database.execute(
                    delete(GuardianRiskReviewRecord).where(
                        GuardianRiskReviewRecord.consent_id.in_(consents)
                    )
                )
                database.execute(
                    delete(GuardianConsentRecord).where(
                        GuardianConsentRecord.consent_id.in_(consents)
                    )
                )
            if invitations:
                database.execute(
                    delete(GuardianInvitationRecord).where(
                        GuardianInvitationRecord.invitation_id.in_(invitations)
                    )
                )
