"""创建用户身份与认证基础表，并写入两个虚拟用户。

Revision ID: 20261027_02
Revises: 20261027_01
Create Date: 2026-10-27
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "20261027_02"
down_revision = "20261027_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    users = op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("phone_masked", sa.String(length=32), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "auth_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_auth_events_user_id", "auth_events", ["user_id"])
    op.create_index("ix_auth_events_event_type", "auth_events", ["event_type"])
    op.create_index("ix_auth_events_result", "auth_events", ["result"])
    op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])

    now = datetime(2026, 10, 27, 20, 0, 0)
    op.bulk_insert(
        users,
        [
            {
                "id": "user_001",
                "display_name": "林安",
                "phone_masked": "138****1024",
                "role": "CUSTOMER",
                "status": "ACTIVE",
                "risk_level": "LOW",
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "user_002",
                "display_name": "周宁",
                "phone_masked": "139****2048",
                "role": "CUSTOMER",
                "status": "ACTIVE",
                "risk_level": "LOW",
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("auth_events")
    op.drop_table("auth_sessions")
    op.drop_table("password_credentials")
    op.drop_table("users")
