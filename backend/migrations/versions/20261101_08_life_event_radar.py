"""主动生活事件雷达：调度状态、租约、审计与飞书投递标识。

Revision ID: 20261101_08
Revises: 20261030_07
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_08"
down_revision = "20261030_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("event_actions", "scheduled_actions")
    op.execute("UPDATE scheduled_actions SET status = 'PENDING' WHERE status = 'SCHEDULED'")
    op.add_column("scheduled_actions", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("scheduled_actions", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("scheduled_actions", sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_actions", sa.Column("lease_owner", sa.String(length=128), nullable=True))
    op.add_column("scheduled_actions", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_actions", sa.Column("claimed_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_actions", sa.Column("sent_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_actions", sa.Column("failed_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_actions", sa.Column("paused_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_actions", sa.Column("canceled_at", sa.DateTime(), nullable=True))
    op.add_column("scheduled_actions", sa.Column("last_error_code", sa.String(length=64), nullable=True))
    # MySQL 对 JSON 默认值的支持随版本变化。先允许 NULL、回填，再收紧约束，
    # 避免依赖数据库特定的 DEFAULT JSON 表达式。
    op.add_column("scheduled_actions", sa.Column("delivery_receipt", sa.JSON(), nullable=True))
    op.execute("UPDATE scheduled_actions SET delivery_receipt = JSON_OBJECT() WHERE delivery_receipt IS NULL")
    op.alter_column(
        "scheduled_actions",
        "delivery_receipt",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.create_index("ix_scheduled_actions_next_attempt_at", "scheduled_actions", ["next_attempt_at"])
    op.create_index("ix_scheduled_actions_lease_owner", "scheduled_actions", ["lease_owner"])
    op.create_index("ix_scheduled_actions_lease_expires_at", "scheduled_actions", ["lease_expires_at"])
    op.create_check_constraint(
        "ck_scheduled_actions_status",
        "scheduled_actions",
        "status IN ('PENDING', 'PROCESSING', 'SENT', 'FAILED', 'PAUSED', 'CANCELED', 'MANUAL_REVIEW')",
    )
    op.create_check_constraint(
        "ck_scheduled_actions_attempts",
        "scheduled_actions",
        "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
    )

    op.create_table(
        "scheduled_action_audits",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["scheduled_actions.action_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index("ix_scheduled_action_audits_action_id", "scheduled_action_audits", ["action_id"])
    op.create_index("ix_scheduled_action_audits_owner_user_id", "scheduled_action_audits", ["owner_user_id"])
    op.create_index("ix_scheduled_action_audits_event_type", "scheduled_action_audits", ["event_type"])
    op.create_index("ix_scheduled_action_audits_to_status", "scheduled_action_audits", ["to_status"])
    op.create_index("ix_scheduled_action_audits_created_at", "scheduled_action_audits", ["created_at"])

    op.add_column("channel_identities", sa.Column("external_user_id_ciphertext", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("channel_identities", "external_user_id_ciphertext")
    op.drop_table("scheduled_action_audits")
    op.drop_constraint("ck_scheduled_actions_attempts", "scheduled_actions", type_="check")
    op.drop_constraint("ck_scheduled_actions_status", "scheduled_actions", type_="check")
    op.drop_index("ix_scheduled_actions_lease_expires_at", table_name="scheduled_actions")
    op.drop_index("ix_scheduled_actions_lease_owner", table_name="scheduled_actions")
    op.drop_index("ix_scheduled_actions_next_attempt_at", table_name="scheduled_actions")
    for column in (
        "delivery_receipt", "last_error_code", "canceled_at", "paused_at", "failed_at",
        "sent_at", "claimed_at", "lease_expires_at", "lease_owner", "next_attempt_at",
        "max_attempts", "attempt_count",
    ):
        op.drop_column("scheduled_actions", column)
    op.execute("UPDATE scheduled_actions SET status = 'SCHEDULED' WHERE status = 'PENDING'")
    op.rename_table("scheduled_actions", "event_actions")
