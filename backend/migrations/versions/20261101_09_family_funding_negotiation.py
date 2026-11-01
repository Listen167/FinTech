"""家庭资金目标协商、审计与有限授权范围。

Revision ID: 20261101_09
Revises: 20261101_08
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_09"
down_revision = "20261101_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "family_funding_negotiations",
        sa.Column("negotiation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("plans", sa.JSON(), nullable=False),
        sa.Column("selected_plan_id", sa.String(length=64), nullable=True),
        sa.Column("baseline_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("candidate_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("workflow_checkpoint", sa.JSON(), nullable=False),
        sa.Column("model_trace", sa.JSON(), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("applied_changes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("selected_at", sa.DateTime(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('WAITING_INPUT', 'WAITING_SELECTION', "
            "'PENDING_CONFIRMATION', 'APPLIED', 'REJECTED', 'CANCELED', "
            "'STALE', 'WAITING_HUMAN', 'UNDONE')",
            name="ck_family_funding_negotiations_status",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("negotiation_id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_family_funding_negotiations_owner_idempotency",
        ),
    )
    for column in (
        "owner_user_id", "source_run_id", "status", "selected_plan_id",
        "baseline_snapshot_hash", "candidate_snapshot_hash", "expires_at",
    ):
        op.create_index(
            f"ix_family_funding_negotiations_{column}",
            "family_funding_negotiations",
            [column],
        )

    op.create_table(
        "family_funding_negotiation_audits",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("negotiation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["negotiation_id"],
            ["family_funding_negotiations.negotiation_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    for column in ("negotiation_id", "owner_user_id", "event_type", "to_status", "created_at"):
        op.create_index(
            f"ix_family_funding_negotiation_audits_{column}",
            "family_funding_negotiation_audits",
            [column],
        )

    op.create_table(
        "funding_consent_scopes",
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("goal_node_ids", sa.JSON(), nullable=False),
        sa.Column("amount_lower_bound", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_upper_bound", sa.Numeric(14, 2), nullable=False),
        sa.Column("protected_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("max_single_adjustment", sa.Numeric(14, 2), nullable=False),
        sa.Column("auto_apply_enabled", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
            name="ck_funding_consent_scopes_status",
        ),
        sa.CheckConstraint(
            "amount_lower_bound >= 0 AND amount_upper_bound >= amount_lower_bound "
            "AND protected_balance >= 0 AND max_single_adjustment >= 0",
            name="ck_funding_consent_scopes_amounts",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("scope_id"),
    )
    for column in ("owner_user_id", "status", "valid_until"):
        op.create_index(
            f"ix_funding_consent_scopes_{column}",
            "funding_consent_scopes",
            [column],
        )


def downgrade() -> None:
    op.drop_table("funding_consent_scopes")
    op.drop_table("family_funding_negotiation_audits")
    op.drop_table("family_funding_negotiations")
