"""创建 Agent 与银行沙箱持久化表。

Revision ID: 20261027_01
Revises:
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20261027_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "agent_sessions",
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_agent_sessions_updated_at", "agent_sessions", ["updated_at"])
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])
    op.create_index("ix_agent_runs_state", "agent_runs", ["state"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])
    op.create_table(
        "confirmations",
        sa.Column("confirmation_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("confirmation_id"),
    )
    op.create_index("ix_confirmations_run_id", "confirmations", ["run_id"])
    op.create_index("ix_confirmations_status", "confirmations", ["status"])
    op.create_table(
        "verifications",
        sa.Column("verification_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("verification_id"),
    )
    op.create_index("ix_verifications_run_id", "verifications", ["run_id"])
    op.create_index("ix_verifications_status", "verifications", ["status"])
    op.create_table(
        "transfer_previews",
        sa.Column("preview_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("preview_id"),
    )
    op.create_index("ix_transfer_previews_status", "transfer_previews", ["status"])
    op.create_table(
        "audit_logs",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index("ix_audit_logs_run_id", "audit_logs", ["run_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_result", "audit_logs", ["result"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_table(
        "idempotency_keys",
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )
    op.create_index("ix_idempotency_keys_run_id", "idempotency_keys", ["run_id"])


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("audit_logs")
    op.drop_table("transfer_previews")
    op.drop_table("verifications")
    op.drop_table("confirmations")
    op.drop_table("agent_runs")
    op.drop_table("agent_sessions")
    op.drop_table("transactions")
    op.drop_table("subscriptions")
    op.drop_table("contacts")
    op.drop_table("accounts")
