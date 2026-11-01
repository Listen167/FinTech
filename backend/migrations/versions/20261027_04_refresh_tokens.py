"""增加 Refresh Token 轮换、历史与 CSRF 绑定。

Revision ID: 20261027_04
Revises: 20261027_03
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20261027_04"
down_revision = "20261027_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "auth_refresh_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("parent_token_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["auth_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_auth_refresh_tokens_session_id", "auth_refresh_tokens", ["session_id"])
    op.create_index("ix_auth_refresh_tokens_user_id", "auth_refresh_tokens", ["user_id"])
    op.create_index("ix_auth_refresh_tokens_status", "auth_refresh_tokens", ["status"])

    # 旧会话没有可验证的浏览器 Refresh Token，迁移时主动撤销，避免产生不可管理会话。
    op.execute(
        sa.text(
            "UPDATE auth_sessions SET revoked_at = UTC_TIMESTAMP(), "
            "revoke_reason = 'REFRESH_MIGRATION' WHERE revoked_at IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_table("auth_refresh_tokens")
    op.drop_column("auth_sessions", "csrf_token_hash")
