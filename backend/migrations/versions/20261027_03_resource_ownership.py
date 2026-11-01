"""为 Agent 与银行沙箱资源增加用户归属。

Revision ID: 20261027_03
Revises: 20261027_02
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20261027_03"
down_revision = "20261027_02"
branch_labels = None
depends_on = None


OWNED_PAYLOAD_TABLES = (
    "accounts",
    "contacts",
    "subscriptions",
    "transactions",
    "agent_sessions",
    "agent_runs",
    "confirmations",
    "verifications",
    "transfer_previews",
    "audit_logs",
)


def _add_owner(table_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "user_id" not in columns:
        op.add_column(
            table_name,
            sa.Column("user_id", sa.String(length=64), server_default="user_001", nullable=False),
        )
    op.execute(sa.text(f"UPDATE {table_name} SET payload = JSON_SET(payload, '$.user_id', user_id)"))
    op.alter_column(
        table_name,
        "user_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
        server_default=None,
    )

    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if f"ix_{table_name}_user_id" not in indexes:
        op.create_index(f"ix_{table_name}_user_id", table_name, ["user_id"])
    foreign_keys = {foreign_key["name"] for foreign_key in inspector.get_foreign_keys(table_name)}
    if f"fk_{table_name}_user_id_users" not in foreign_keys:
        op.create_foreign_key(
            f"fk_{table_name}_user_id_users",
            table_name,
            "users",
            ["user_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def upgrade() -> None:
    for table_name in OWNED_PAYLOAD_TABLES:
        _add_owner(table_name)

    inspector = sa.inspect(op.get_bind())
    idempotency_columns = {
        column["name"] for column in inspector.get_columns("idempotency_keys")
    }
    if "user_id" not in idempotency_columns:
        op.add_column(
            "idempotency_keys",
            sa.Column("user_id", sa.String(length=64), server_default="user_001", nullable=False),
        )
    if "client_key" not in idempotency_columns:
        op.add_column(
            "idempotency_keys", sa.Column("client_key", sa.String(length=255), nullable=True)
        )
    op.execute(sa.text("UPDATE idempotency_keys SET client_key = idempotency_key"))
    op.execute(
        sa.text(
            "UPDATE idempotency_keys "
            "SET idempotency_key = SHA2(CONCAT(user_id, ':', client_key), 256)"
        )
    )
    op.alter_column(
        "idempotency_keys",
        "user_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
        server_default=None,
    )
    op.alter_column(
        "idempotency_keys",
        "client_key",
        existing_type=sa.String(length=255),
        existing_nullable=True,
        nullable=False,
    )

    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("idempotency_keys")}
    if "ix_idempotency_keys_user_id" not in indexes:
        op.create_index("ix_idempotency_keys_user_id", "idempotency_keys", ["user_id"])
    foreign_keys = {
        foreign_key["name"] for foreign_key in inspector.get_foreign_keys("idempotency_keys")
    }
    if "fk_idempotency_keys_user_id_users" not in foreign_keys:
        op.create_foreign_key(
            "fk_idempotency_keys_user_id_users",
            "idempotency_keys",
            "users",
            ["user_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_idempotency_keys_user_id_users", "idempotency_keys", type_="foreignkey"
    )
    op.drop_index("ix_idempotency_keys_user_id", table_name="idempotency_keys")
    op.execute(sa.text("UPDATE idempotency_keys SET idempotency_key = client_key"))
    op.drop_column("idempotency_keys", "client_key")
    op.drop_column("idempotency_keys", "user_id")

    for table_name in reversed(OWNED_PAYLOAD_TABLES):
        op.drop_constraint(
            f"fk_{table_name}_user_id_users", table_name, type_="foreignkey"
        )
        op.drop_index(f"ix_{table_name}_user_id", table_name=table_name)
        op.drop_column(table_name, "user_id")
