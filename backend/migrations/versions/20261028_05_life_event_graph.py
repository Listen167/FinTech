"""建立生活关系、事件、目标与动作图数据表。

Revision ID: 20261028_05
Revises: 20261027_04
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20261028_05"
down_revision = "20261027_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "life_graph_nodes",
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("display_label", sa.String(length=100), nullable=False),
        sa.Column("sensitivity_level", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("node_id"),
        sa.UniqueConstraint(
            "owner_user_id", "node_id", name="uq_life_graph_nodes_owner_node"
        ),
    )
    op.create_index("ix_life_graph_nodes_owner_user_id", "life_graph_nodes", ["owner_user_id"])
    op.create_index("ix_life_graph_nodes_node_type", "life_graph_nodes", ["node_type"])
    op.create_index(
        "ix_life_graph_nodes_sensitivity_level", "life_graph_nodes", ["sensitivity_level"]
    )
    op.create_index("ix_life_graph_nodes_status", "life_graph_nodes", ["status"])

    op.create_table(
        "life_graph_edges",
        sa.Column("edge_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("source_node_id", sa.String(length=64), nullable=False),
        sa.Column("target_node_id", sa.String(length=64), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("consent_scope", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("confidence_bp", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence_bp >= 0 AND confidence_bp <= 10000",
            name="ck_life_graph_edges_confidence_bp",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "source_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_life_graph_edges_source_owner",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "target_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_life_graph_edges_target_owner",
        ),
        sa.PrimaryKeyConstraint("edge_id"),
    )
    op.create_index("ix_life_graph_edges_owner_user_id", "life_graph_edges", ["owner_user_id"])
    op.create_index("ix_life_graph_edges_source_node_id", "life_graph_edges", ["source_node_id"])
    op.create_index("ix_life_graph_edges_target_node_id", "life_graph_edges", ["target_node_id"])
    op.create_index("ix_life_graph_edges_relation_type", "life_graph_edges", ["relation_type"])
    op.create_index("ix_life_graph_edges_provenance", "life_graph_edges", ["provenance"])
    op.create_index("ix_life_graph_edges_status", "life_graph_edges", ["status"])

    op.create_table(
        "life_events",
        sa.Column("event_node_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurs_at", sa.DateTime(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("recurrence_rule", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "event_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_life_events_node_owner",
        ),
        sa.PrimaryKeyConstraint("event_node_id"),
        sa.UniqueConstraint(
            "owner_user_id", "event_node_id", name="uq_life_events_owner_event"
        ),
    )
    op.create_index("ix_life_events_owner_user_id", "life_events", ["owner_user_id"])
    op.create_index("ix_life_events_event_type", "life_events", ["event_type"])
    op.create_index("ix_life_events_occurs_at", "life_events", ["occurs_at"])
    op.create_index("ix_life_events_status", "life_events", ["status"])

    op.create_table(
        "financial_goals",
        sa.Column("goal_node_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("target_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reserved_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("funding_policy", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("target_amount >= 0", name="ck_financial_goals_target_amount"),
        sa.CheckConstraint("reserved_amount >= 0", name="ck_financial_goals_reserved_amount"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "goal_node_id"],
            ["life_graph_nodes.owner_user_id", "life_graph_nodes.node_id"],
            ondelete="CASCADE",
            name="fk_financial_goals_node_owner",
        ),
        sa.PrimaryKeyConstraint("goal_node_id"),
        sa.UniqueConstraint(
            "owner_user_id", "goal_node_id", name="uq_financial_goals_owner_goal"
        ),
    )
    op.create_index("ix_financial_goals_owner_user_id", "financial_goals", ["owner_user_id"])
    op.create_index("ix_financial_goals_due_at", "financial_goals", ["due_at"])
    op.create_index("ix_financial_goals_status", "financial_goals", ["status"])

    op.create_table(
        "event_actions",
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("event_node_id", sa.String(length=64), nullable=True),
        sa.Column("goal_node_id", sa.String(length=64), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "event_node_id IS NOT NULL OR goal_node_id IS NOT NULL",
            name="ck_event_actions_has_subject",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "event_node_id"],
            ["life_events.owner_user_id", "life_events.event_node_id"],
            ondelete="CASCADE",
            name="fk_event_actions_event_owner",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "goal_node_id"],
            ["financial_goals.owner_user_id", "financial_goals.goal_node_id"],
            ondelete="CASCADE",
            name="fk_event_actions_goal_owner",
        ),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint(
            "owner_user_id", "idempotency_key", name="uq_event_actions_owner_idempotency"
        ),
    )
    op.create_index("ix_event_actions_owner_user_id", "event_actions", ["owner_user_id"])
    op.create_index("ix_event_actions_event_node_id", "event_actions", ["event_node_id"])
    op.create_index("ix_event_actions_goal_node_id", "event_actions", ["goal_node_id"])
    op.create_index("ix_event_actions_action_type", "event_actions", ["action_type"])
    op.create_index("ix_event_actions_scheduled_at", "event_actions", ["scheduled_at"])
    op.create_index("ix_event_actions_status", "event_actions", ["status"])


def downgrade() -> None:
    op.drop_table("event_actions")
    op.drop_table("financial_goals")
    op.drop_table("life_events")
    op.drop_table("life_graph_edges")
    op.drop_table("life_graph_nodes")
