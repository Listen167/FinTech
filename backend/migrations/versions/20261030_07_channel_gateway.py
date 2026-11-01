"""建立 Channel Gateway 身份绑定与消息幂等表。

Revision ID: 20261030_07
Revises: 20261028_06
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20261030_07"
down_revision = "20261028_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_link_tokens",
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'USED', 'EXPIRED', 'CANCELED')",
            name="ck_channel_link_tokens_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_channel_link_tokens_user_id", "channel_link_tokens", ["user_id"])
    op.create_index("ix_channel_link_tokens_channel", "channel_link_tokens", ["channel"])
    op.create_index("ix_channel_link_tokens_status", "channel_link_tokens", ["status"])
    op.create_index("ix_channel_link_tokens_expires_at", "channel_link_tokens", ["expires_at"])

    op.create_table(
        "channel_identities",
        sa.Column("identity_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("external_user_id_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED')",
            name="ck_channel_identities_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("identity_id"),
        sa.UniqueConstraint(
            "channel",
            "tenant_id",
            "external_user_id_hash",
            name="uq_channel_identity_external",
        ),
    )
    op.create_index("ix_channel_identities_user_id", "channel_identities", ["user_id"])
    op.create_index("ix_channel_identities_channel", "channel_identities", ["channel"])
    op.create_index("ix_channel_identities_status", "channel_identities", ["status"])

    op.create_table(
        "channel_conversations",
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("identity_id", sa.String(length=64), nullable=False),
        sa.Column("external_conversation_id_hash", sa.String(length=64), nullable=False),
        sa.Column("agent_session_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'CLOSED')",
            name="ck_channel_conversations_status",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["channel_identities.identity_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
        sa.UniqueConstraint(
            "identity_id",
            "external_conversation_id_hash",
            name="uq_channel_conversation_external",
        ),
    )
    op.create_index(
        "ix_channel_conversations_identity_id", "channel_conversations", ["identity_id"]
    )
    op.create_index(
        "ix_channel_conversations_agent_session_id",
        "channel_conversations",
        ["agent_session_id"],
    )
    op.create_index("ix_channel_conversations_status", "channel_conversations", ["status"])

    op.create_table(
        "channel_messages",
        sa.Column("gateway_message_id", sa.String(length=64), nullable=False),
        sa.Column("identity_id", sa.String(length=64), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("external_message_id_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("message_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "message_kind IN ('TEXT', 'LINK')",
            name="ck_channel_messages_kind",
        ),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'PROCESSED', 'REJECTED', 'FAILED')",
            name="ck_channel_messages_status",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["channel_identities.identity_id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["channel_conversations.conversation_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("gateway_message_id"),
        sa.UniqueConstraint(
            "channel",
            "tenant_id",
            "external_message_id_hash",
            name="uq_channel_message_external",
        ),
    )
    op.create_index("ix_channel_messages_identity_id", "channel_messages", ["identity_id"])
    op.create_index(
        "ix_channel_messages_conversation_id", "channel_messages", ["conversation_id"]
    )
    op.create_index("ix_channel_messages_channel", "channel_messages", ["channel"])
    op.create_index("ix_channel_messages_status", "channel_messages", ["status"])
    op.create_index("ix_channel_messages_run_id", "channel_messages", ["run_id"])
    op.create_index("ix_channel_messages_error_code", "channel_messages", ["error_code"])


def downgrade() -> None:
    op.drop_table("channel_messages")
    op.drop_table("channel_conversations")
    op.drop_table("channel_identities")
    op.drop_table("channel_link_tokens")
