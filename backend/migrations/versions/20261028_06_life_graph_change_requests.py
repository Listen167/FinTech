"""建立生活图谱候选变更请求表。

Revision ID: 20261028_06
Revises: 20261028_05
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20261028_06"
down_revision = "20261028_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "life_graph_change_requests",
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("change_set", sa.JSON(), nullable=False),
        sa.Column("decision_reason", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELED')",
            name="ck_life_graph_change_requests_status",
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name="ck_life_graph_change_requests_schema_version",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("request_id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_life_graph_change_requests_owner_idempotency",
        ),
    )
    op.create_index(
        "ix_life_graph_change_requests_owner_user_id",
        "life_graph_change_requests",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_life_graph_change_requests_source_run_id",
        "life_graph_change_requests",
        ["source_run_id"],
    )
    op.create_index(
        "ix_life_graph_change_requests_source",
        "life_graph_change_requests",
        ["source"],
    )
    op.create_index(
        "ix_life_graph_change_requests_status",
        "life_graph_change_requests",
        ["status"],
    )
    op.create_index(
        "ix_life_graph_change_requests_expires_at",
        "life_graph_change_requests",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("life_graph_change_requests")
