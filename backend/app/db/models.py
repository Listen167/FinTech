"""Agent 状态与银行沙箱的 MySQL 表模型。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_masked: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="CUSTOMER", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="LOW")
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now, onupdate=utc_now)

    credential: Mapped["PasswordCredentialRecord | None"] = relationship(back_populates="user")


class PasswordCredentialRecord(Base):
    __tablename__ = "password_credentials"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime())
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)

    user: Mapped[UserRecord] = relationship(back_populates="credential")


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime())
    revoke_reason: Mapped[str | None] = mapped_column(String(100))


class RefreshTokenRecord(Base):
    __tablename__ = "auth_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    parent_token_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime())


class AuthEventRecord(Base):
    __tablename__ = "auth_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now, index=True)


class AccountRecord(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ContactRecord(Base):
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SubscriptionRecord(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class TransactionRecord(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AgentSessionRecord(Base):
    __tablename__ = "agent_sessions"
    session_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    updated_at: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AgentRunRecord(Base):
    __tablename__ = "agent_runs"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ConfirmationRecord(Base):
    __tablename__ = "confirmations"
    confirmation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class VerificationRecord(Base):
    __tablename__ = "verifications"
    verification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class TransferPreviewRecord(Base):
    __tablename__ = "transfer_previews"
    preview_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    client_key: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), index=True)


class LifeGraphNodeRecord(Base):
    __tablename__ = "life_graph_nodes"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "node_id", name="uq_life_graph_nodes_owner_node"),
    )

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    display_label: Mapped[str] = mapped_column(String(100), nullable=False)
    sensitivity_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PERSONAL", index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class LifeGraphEdgeRecord(Base):
    __tablename__ = "life_graph_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "source_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_life_graph_edges_source_owner",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "target_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_life_graph_edges_target_owner",
        ),
        CheckConstraint(
            "confidence_bp >= 0 AND confidence_bp <= 10000",
            name="ck_life_graph_edges_confidence_bp",
        ),
    )

    edge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    consent_scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    provenance: Mapped[str] = mapped_column(
        String(32), nullable=False, default="USER_DECLARED", index=True
    )
    confidence_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime())
    valid_until: Mapped[datetime | None] = mapped_column(DateTime())
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class LifeEventRecord(Base):
    __tablename__ = "life_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "event_node_id", name="uq_life_events_owner_event"
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "event_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_life_events_node_owner",
        ),
    )

    event_node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurs_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    recurrence_rule: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class FinancialGoalRecord(Base):
    __tablename__ = "financial_goals"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "goal_node_id", name="uq_financial_goals_owner_goal"
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "goal_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_financial_goals_node_owner",
        ),
        CheckConstraint("target_amount >= 0", name="ck_financial_goals_target_amount"),
        CheckConstraint("reserved_amount >= 0", name="ck_financial_goals_reserved_amount"),
    )

    goal_node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reserved_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(), index=True)
    funding_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class EventActionRecord(Base):
    __tablename__ = "scheduled_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "event_node_id"],
            ["life_events.owner_user_id", "life_events.event_node_id"],
            ondelete="CASCADE",
            name="fk_event_actions_event_owner",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "goal_node_id"],
            ["financial_goals.owner_user_id", "financial_goals.goal_node_id"],
            ondelete="CASCADE",
            name="fk_event_actions_goal_owner",
        ),
        UniqueConstraint(
            "owner_user_id", "idempotency_key", name="uq_event_actions_owner_idempotency"
        ),
        CheckConstraint(
            "event_node_id IS NOT NULL OR goal_node_id IS NOT NULL",
            name="ck_event_actions_has_subject",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'SENT', 'FAILED', 'PAUSED', "
            "'CANCELED', 'MANUAL_REVIEW')",
            name="ck_scheduled_actions_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_scheduled_actions_attempts",
        ),
    )

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_node_id: Mapped[str | None] = mapped_column(String(64), index=True)
    goal_node_id: Mapped[str | None] = mapped_column(String(64), index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime())
    failed_at: Mapped[datetime | None] = mapped_column(DateTime())
    paused_at: Mapped[datetime | None] = mapped_column(DateTime())
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    delivery_receipt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class EventActionAuditRecord(Base):
    __tablename__ = "scheduled_action_audits"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("scheduled_actions.action_id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now, index=True)


class LifeGraphChangeRequestRecord(Base):
    __tablename__ = "life_graph_change_requests"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_life_graph_change_requests_owner_idempotency",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELED')",
            name="ck_life_graph_change_requests_status",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="ck_life_graph_change_requests_schema_version",
        ),
    )

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="STRUCTURED_INPUT", index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", index=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    change_set: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class FamilyFundingNegotiationRecord(Base):
    __tablename__ = "family_funding_negotiations"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_family_funding_negotiations_owner_idempotency",
        ),
        CheckConstraint(
            "status IN ('WAITING_INPUT', 'WAITING_SELECTION', "
            "'PENDING_CONFIRMATION', 'APPLIED', 'REJECTED', 'CANCELED', "
            "'STALE', 'WAITING_HUMAN', 'UNDONE')",
            name="ck_family_funding_negotiations_status",
        ),
    )

    negotiation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_text: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    plans: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    selected_plan_id: Mapped[str | None] = mapped_column(String(64), index=True)
    baseline_snapshot_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    candidate_snapshot_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    workflow_checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model_trace: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    applied_changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class FamilyFundingNegotiationAuditRecord(Base):
    __tablename__ = "family_funding_negotiation_audits"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    negotiation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("family_funding_negotiations.negotiation_id", ondelete="CASCADE"),
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now, index=True)


class FundingConsentScopeRecord(Base):
    __tablename__ = "funding_consent_scopes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
            name="ck_funding_consent_scopes_status",
        ),
        CheckConstraint(
            "amount_lower_bound >= 0 AND amount_upper_bound >= amount_lower_bound "
            "AND protected_balance >= 0 AND max_single_adjustment >= 0",
            name="ck_funding_consent_scopes_amounts",
        ),
    )

    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_node_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    amount_lower_bound: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_upper_bound: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    protected_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    max_single_adjustment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    auto_apply_enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class ChannelLinkTokenRecord(Base):
    __tablename__ = "channel_link_tokens"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'USED', 'EXPIRED', 'CANCELED')",
            name="ck_channel_link_tokens_status",
        ),
    )

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)


class ChannelIdentityRecord(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "tenant_id",
            "external_user_id_hash",
            name="uq_channel_identity_external",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED')",
            name="ck_channel_identities_status",
        ),
    )

    identity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_user_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    external_user_id_ciphertext: Mapped[str | None] = mapped_column(Text())
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", index=True
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class ChannelConversationRecord(Base):
    __tablename__ = "channel_conversations"
    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            "external_conversation_id_hash",
            name="uq_channel_conversation_external",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'CLOSED')",
            name="ck_channel_conversations_status",
        ),
    )

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("channel_identities.identity_id", ondelete="CASCADE"),
        index=True,
    )
    external_conversation_id_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    # Agent Runtime 会重写 agent_sessions 表，因此这里只保留稳定引用，不建立外键。
    agent_session_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class ChannelMessageRecord(Base):
    __tablename__ = "channel_messages"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "tenant_id",
            "external_message_id_hash",
            name="uq_channel_message_external",
        ),
        CheckConstraint(
            "message_kind IN ('TEXT', 'LINK')",
            name="ck_channel_messages_kind",
        ),
        CheckConstraint(
            "status IN ('PROCESSING', 'PROCESSED', 'REJECTED', 'FAILED')",
            name="ck_channel_messages_status",
        ),
    )

    gateway_message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("channel_identities.identity_id", ondelete="SET NULL"),
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("channel_conversations.conversation_id", ondelete="SET NULL"),
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_message_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    message_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PROCESSING", index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), index=True)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class ChannelWorkerLeaseRecord(Base):
    __tablename__ = "channel_worker_leases"
    __table_args__ = (
        CheckConstraint(
            "worker_kind IN ('FEISHU_INBOUND', 'LIFE_RADAR')",
            name="ck_channel_worker_leases_kind",
        ),
        CheckConstraint(
            "transport IN ('LONG_CONNECTION', 'WEBHOOK', 'SCHEDULER')",
            name="ck_channel_worker_leases_transport",
        ),
        CheckConstraint(
            "state IN ('STARTING', 'ONLINE', 'DEGRADED', 'STOPPING')",
            name="ck_channel_worker_leases_state",
        ),
    )

    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    worker_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    transport: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class FraudRiskEventRecord(Base):
    __tablename__ = "fraud_risk_events"
    __table_args__ = (
        UniqueConstraint("active_scope_key", name="uq_fraud_risk_events_active_scope"),
        CheckConstraint(
            "status IN ('COOLING_OFF', 'RELEASED', 'EXPIRED')",
            name="ck_fraud_risk_events_status",
        ),
        CheckConstraint(
            "appeal_status IN ('NONE', 'SUBMITTED')",
            name="ck_fraud_risk_events_appeal_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_fraud_risk_events_retry_count"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    trigger_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    recipient_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    active_scope_key: Mapped[str | None] = mapped_column(String(160))
    recipient_display: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient_account_masked: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="COOLING_OFF", index=True
    )
    signals: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    message_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    appeal_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NONE", index=True
    )
    appeal_reason: Mapped[str | None] = mapped_column(Text())
    appeal_submitted_at: Mapped[datetime | None] = mapped_column(DateTime())
    triggered_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    cooling_until: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime())
    release_method: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class FraudReleaseChallengeRecord(Base):
    __tablename__ = "fraud_release_challenges"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'VERIFIED', 'EXPIRED', 'LOCKED', 'SUPERSEDED')",
            name="ck_fraud_release_challenges_status",
        ),
        CheckConstraint("failed_attempts >= 0", name="ck_fraud_release_challenges_attempts"),
    )

    challenge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("fraud_risk_events.event_id", ondelete="CASCADE"),
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", index=True
    )
    otp_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_channel: Mapped[str] = mapped_column(String(32), nullable=False, default="SMS")
    issued_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class FraudRiskAuditRecord(Base):
    __tablename__ = "fraud_risk_audits"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("fraud_risk_events.event_id", ondelete="CASCADE"),
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now, index=True)


class GuardianInvitationRecord(Base):
    __tablename__ = "guardian_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'CANCELED', 'EXPIRED')",
            name="ck_guardian_invitations_status",
        ),
        CheckConstraint(
            "amount_lower_bound >= 0 AND amount_upper_bound >= amount_lower_bound",
            name="ck_guardian_invitations_amounts",
        ),
    )

    invitation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    guardian_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scenarios: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    amount_lower_bound: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_upper_bound: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    visible_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    consent_valid_until: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class GuardianConsentRecord(Base):
    __tablename__ = "guardian_consents"
    __table_args__ = (
        UniqueConstraint("invitation_id", name="uq_guardian_consents_invitation"),
        UniqueConstraint("active_pair_key", name="uq_guardian_consents_active_pair"),
        CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
            name="ck_guardian_consents_status",
        ),
        CheckConstraint(
            "amount_lower_bound >= 0 AND amount_upper_bound >= amount_lower_bound",
            name="ck_guardian_consents_amounts",
        ),
    )

    consent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    invitation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("guardian_invitations.invitation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    guardian_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    active_pair_key: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scenarios: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    amount_lower_bound: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_upper_bound: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    visible_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    activated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime())
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL")
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class GuardianRiskReviewRecord(Base):
    __tablename__ = "guardian_risk_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AWAITING_OWNER', 'AWAITING_GUARDIAN', 'ACKNOWLEDGED', "
            "'REJECTED', 'EXPIRED', 'CANCELED')",
            name="ck_guardian_risk_reviews_status",
        ),
        CheckConstraint("amount >= 0", name="ck_guardian_risk_reviews_amount"),
    )

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consent_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("guardian_consents.consent_id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    guardian_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_masked: Mapped[str | None] = mapped_column(String(64))
    risk_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    visible_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_note: Mapped[str | None] = mapped_column(String(500))
    guardian_note: Mapped[str | None] = mapped_column(String(500))
    owner_decided_at: Mapped[datetime | None] = mapped_column(DateTime())
    guardian_decided_at: Mapped[datetime | None] = mapped_column(DateTime())
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class GuardianAuditRecord(Base):
    __tablename__ = "guardian_audits"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consent_id: Mapped[str | None] = mapped_column(String(64), index=True)
    invitation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    review_id: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    guardian_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now, index=True)
