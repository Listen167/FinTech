"""家庭守护邀请、最小授权、双确认与审计。

Revision ID: 20261101_11
Revises: 20261101_10
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_11"
down_revision = "20261101_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guardian_invitations",
        sa.Column("invitation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("guardian_user_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scenarios", sa.JSON(), nullable=False),
        sa.Column("amount_lower_bound", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_upper_bound", sa.Numeric(14, 2), nullable=False),
        sa.Column("visible_fields", sa.JSON(), nullable=False),
        sa.Column("consent_valid_until", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'CANCELED', 'EXPIRED')",
            name="ck_guardian_invitations_status",
        ),
        sa.CheckConstraint(
            "amount_lower_bound >= 0 AND amount_upper_bound >= amount_lower_bound",
            name="ck_guardian_invitations_amounts",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("invitation_id"),
        sa.UniqueConstraint("token_hash", name="uq_guardian_invitations_token_hash"),
    )
    for column in (
        "owner_user_id", "guardian_user_id", "status", "consent_valid_until", "expires_at",
    ):
        op.create_index(
            f"ix_guardian_invitations_{column}", "guardian_invitations", [column]
        )

    op.create_table(
        "guardian_consents",
        sa.Column("consent_id", sa.String(length=64), nullable=False),
        sa.Column("invitation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("guardian_user_id", sa.String(length=64), nullable=False),
        sa.Column("active_pair_key", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scenarios", sa.JSON(), nullable=False),
        sa.Column("amount_lower_bound", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_upper_bound", sa.Numeric(14, 2), nullable=False),
        sa.Column("visible_fields", sa.JSON(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("revoke_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
            name="ck_guardian_consents_status",
        ),
        sa.CheckConstraint(
            "amount_lower_bound >= 0 AND amount_upper_bound >= amount_lower_bound",
            name="ck_guardian_consents_amounts",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["guardian_invitations.invitation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("consent_id"),
        sa.UniqueConstraint("invitation_id", name="uq_guardian_consents_invitation"),
        sa.UniqueConstraint("active_pair_key", name="uq_guardian_consents_active_pair"),
    )
    for column in (
        "owner_user_id", "guardian_user_id", "status", "valid_until",
    ):
        op.create_index(f"ix_guardian_consents_{column}", "guardian_consents", [column])

    op.create_table(
        "guardian_risk_reviews",
        sa.Column("review_id", sa.String(length=64), nullable=False),
        sa.Column("consent_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("guardian_user_id", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=True),
        sa.Column("scenario", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("recipient_masked", sa.String(length=64), nullable=True),
        sa.Column("risk_summary", sa.String(length=500), nullable=False),
        sa.Column("visible_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_note", sa.String(length=500), nullable=True),
        sa.Column("guardian_note", sa.String(length=500), nullable=True),
        sa.Column("owner_decided_at", sa.DateTime(), nullable=True),
        sa.Column("guardian_decided_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('AWAITING_OWNER', 'AWAITING_GUARDIAN', 'ACKNOWLEDGED', "
            "'REJECTED', 'EXPIRED', 'CANCELED')",
            name="ck_guardian_risk_reviews_status",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_guardian_risk_reviews_amount"),
        sa.ForeignKeyConstraint(
            ["consent_id"], ["guardian_consents.consent_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("review_id"),
    )
    for column in (
        "consent_id", "owner_user_id", "guardian_user_id", "source_run_id",
        "scenario", "status", "expires_at",
    ):
        op.create_index(
            f"ix_guardian_risk_reviews_{column}", "guardian_risk_reviews", [column]
        )

    op.create_table(
        "guardian_audits",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("consent_id", sa.String(length=64), nullable=True),
        sa.Column("invitation_id", sa.String(length=64), nullable=True),
        sa.Column("review_id", sa.String(length=64), nullable=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("guardian_user_id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    for column in (
        "consent_id", "invitation_id", "review_id", "owner_user_id",
        "guardian_user_id", "actor_user_id", "event_type", "result", "created_at",
    ):
        op.create_index(f"ix_guardian_audits_{column}", "guardian_audits", [column])


def downgrade() -> None:
    op.drop_table("guardian_audits")
    op.drop_table("guardian_risk_reviews")
    op.drop_table("guardian_consents")
    op.drop_table("guardian_invitations")
