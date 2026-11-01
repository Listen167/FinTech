"""反诈冷静期风险事件、释放挑战与审计。

Revision ID: 20261101_10
Revises: 20261101_09
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_10"
down_revision = "20261101_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fraud_risk_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("trigger_run_id", sa.String(length=64), nullable=True),
        sa.Column("recipient_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("active_scope_key", sa.String(length=160), nullable=True),
        sa.Column("recipient_display", sa.String(length=100), nullable=False),
        sa.Column("recipient_account_masked", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("message_digest", sa.String(length=64), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("appeal_status", sa.String(length=32), nullable=False),
        sa.Column("appeal_reason", sa.Text(), nullable=True),
        sa.Column("appeal_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("triggered_at", sa.DateTime(), nullable=False),
        sa.Column("cooling_until", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("release_method", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('COOLING_OFF', 'RELEASED', 'EXPIRED')",
            name="ck_fraud_risk_events_status",
        ),
        sa.CheckConstraint(
            "appeal_status IN ('NONE', 'SUBMITTED')",
            name="ck_fraud_risk_events_appeal_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_fraud_risk_events_retry_count"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("active_scope_key", name="uq_fraud_risk_events_active_scope"),
    )
    for column in (
        "owner_user_id", "trigger_run_id", "recipient_fingerprint", "status",
        "appeal_status", "triggered_at", "cooling_until",
    ):
        op.create_index(f"ix_fraud_risk_events_{column}", "fraud_risk_events", [column])

    op.create_table(
        "fraud_release_challenges",
        sa.Column("challenge_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("otp_hash", sa.String(length=64), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("delivery_channel", sa.String(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'VERIFIED', 'EXPIRED', 'LOCKED', 'SUPERSEDED')",
            name="ck_fraud_release_challenges_status",
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0", name="ck_fraud_release_challenges_attempts"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["fraud_risk_events.event_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("challenge_id"),
    )
    for column in ("event_id", "owner_user_id", "status", "expires_at"):
        op.create_index(
            f"ix_fraud_release_challenges_{column}",
            "fraud_release_challenges",
            [column],
        )

    op.create_table(
        "fraud_risk_audits",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"], ["fraud_risk_events.event_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    for column in ("event_id", "owner_user_id", "event_type", "created_at"):
        op.create_index(f"ix_fraud_risk_audits_{column}", "fraud_risk_audits", [column])


def downgrade() -> None:
    op.drop_table("fraud_risk_audits")
    op.drop_table("fraud_release_challenges")
    op.drop_table("fraud_risk_events")

