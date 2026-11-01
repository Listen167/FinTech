"""渠道身份活跃状态与 Worker 在线租约。

Revision ID: 20261101_12
Revises: 20261101_11
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_12"
down_revision = "20261101_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_identities",
        sa.Column("last_active_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_channel_identities_last_active_at",
        "channel_identities",
        ["last_active_at"],
    )

    op.create_table(
        "channel_worker_leases",
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("worker_kind", sa.String(length=32), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "worker_kind IN ('FEISHU_INBOUND', 'LIFE_RADAR')",
            name="ck_channel_worker_leases_kind",
        ),
        sa.CheckConstraint(
            "transport IN ('LONG_CONNECTION', 'WEBHOOK', 'SCHEDULER')",
            name="ck_channel_worker_leases_transport",
        ),
        sa.CheckConstraint(
            "state IN ('STARTING', 'ONLINE', 'DEGRADED', 'STOPPING')",
            name="ck_channel_worker_leases_state",
        ),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "ix_channel_worker_leases_worker_kind",
        "channel_worker_leases",
        ["worker_kind"],
    )
    op.create_index(
        "ix_channel_worker_leases_transport",
        "channel_worker_leases",
        ["transport"],
    )
    op.create_index(
        "ix_channel_worker_leases_state",
        "channel_worker_leases",
        ["state"],
    )
    op.create_index(
        "ix_channel_worker_leases_heartbeat_at",
        "channel_worker_leases",
        ["heartbeat_at"],
    )
    op.create_index(
        "ix_channel_worker_leases_expires_at",
        "channel_worker_leases",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("channel_worker_leases")
    op.drop_index(
        "ix_channel_identities_last_active_at",
        table_name="channel_identities",
    )
    op.drop_column("channel_identities", "last_active_at")
