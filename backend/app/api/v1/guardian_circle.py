"""家庭守护圈邀请、Consent、风险双确认和审计接口。"""

from fastapi import APIRouter, Depends, status

from backend.app.dependencies.auth import get_current_auth
from backend.app.dependencies.guardian_circle import get_guardian_circle_service
from backend.app.schemas.guardian_circle import (
    CreateGuardianInvitationRequest,
    CreateGuardianRiskReviewRequest,
    GuardianInvitationTokenRequest,
    GuardianOwnerDecisionRequest,
    GuardianReasonRequest,
    GuardianRiskDecisionRequest,
)
from backend.app.services.auth_service import AuthContext
from backend.app.services.guardian_circle import GuardianCircleService


router = APIRouter()


@router.get("/candidates")
def list_candidates(
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    items = service.list_candidates(context.user_id)
    return {"items": items, "total": len(items)}


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
def create_invitation(
    payload: CreateGuardianInvitationRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.create_invitation(owner_user_id=context.user_id, payload=payload)


@router.get("/invitations")
def list_invitations(
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    items = service.list_invitations(context.user_id)
    return {"items": items, "total": len(items)}


@router.post("/invitations/accept")
def accept_invitation(
    payload: GuardianInvitationTokenRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.accept_invitation(
        guardian_user_id=context.user_id,
        token=payload.token,
    )


@router.post("/invitations/reject")
def reject_invitation(
    payload: GuardianInvitationTokenRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.reject_invitation(
        guardian_user_id=context.user_id,
        token=payload.token,
        reason=payload.reason,
    )


@router.post("/invitations/{invitation_id}/cancel")
def cancel_invitation(
    invitation_id: str,
    payload: GuardianReasonRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.cancel_invitation(
        owner_user_id=context.user_id,
        invitation_id=invitation_id,
        reason=payload.reason,
    )


@router.get("/consents")
def list_consents(
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    items = service.list_consents(context.user_id)
    return {"items": items, "total": len(items)}


@router.post("/consents/{consent_id}/revoke")
def revoke_consent(
    consent_id: str,
    payload: GuardianReasonRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.revoke_consent(
        actor_user_id=context.user_id,
        consent_id=consent_id,
        reason=payload.reason,
    )


@router.get("/consents/{consent_id}/audits")
def list_consent_audits(
    consent_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    items = service.audits(actor_user_id=context.user_id, consent_id=consent_id)
    return {"items": items, "total": len(items)}


@router.post("/risk-reviews", status_code=status.HTTP_201_CREATED)
def create_risk_review(
    payload: CreateGuardianRiskReviewRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.create_risk_review(owner_user_id=context.user_id, payload=payload)


@router.get("/risk-reviews")
def list_risk_reviews(
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    items = service.list_risk_reviews(context.user_id)
    return {"items": items, "total": len(items)}


@router.get("/risk-reviews/{review_id}")
def get_risk_review(
    review_id: str,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.get_risk_review(
        actor_user_id=context.user_id,
        review_id=review_id,
    )


@router.post("/risk-reviews/{review_id}/owner-decision")
def owner_decision(
    review_id: str,
    payload: GuardianOwnerDecisionRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.owner_decide(
        owner_user_id=context.user_id,
        review_id=review_id,
        confirmed=payload.confirmed,
        note=payload.note,
    )


@router.post("/risk-reviews/{review_id}/guardian-decision")
def guardian_decision(
    review_id: str,
    payload: GuardianRiskDecisionRequest,
    context: AuthContext = Depends(get_current_auth),
    service: GuardianCircleService = Depends(get_guardian_circle_service),
) -> dict:
    return service.guardian_decide(
        guardian_user_id=context.user_id,
        review_id=review_id,
        risk_acknowledged=payload.risk_acknowledged,
        note=payload.note,
    )
