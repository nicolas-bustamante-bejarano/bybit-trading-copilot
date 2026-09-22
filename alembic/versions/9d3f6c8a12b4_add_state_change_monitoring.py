"""add state change monitoring

Revision ID: 9d3f6c8a12b4
Revises: 4b7d594785d9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9d3f6c8a12b4"
down_revision: str | Sequence[str] | None = "4b7d594785d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coach_state_cursors",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("position_open", sa.Boolean(), nullable=False),
        sa.Column("trade_plan_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trade_plan_id"], ["trade_plans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_index(
        op.f("ix_coach_state_cursors_trade_plan_id"),
        "coach_state_cursors",
        ["trade_plan_id"],
        unique=False,
    )
    op.create_table(
        "state_change_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("trade_plan_id", sa.String(length=36), nullable=True),
        sa.Column("decision_snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("importance", sa.String(length=16), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("state_before", sa.JSON(), nullable=False),
        sa.Column("state_after", sa.JSON(), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("confidence_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_snapshot_id"], ["decision_snapshots.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["trade_plan_id"], ["trade_plans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "to_version", name="uq_state_change_symbol_version"),
    )
    op.create_index(
        op.f("ix_state_change_events_event_type"),
        "state_change_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_state_change_events_symbol"),
        "state_change_events",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_state_change_events_timestamp"),
        "state_change_events",
        ["timestamp"],
        unique=False,
    )
    op.create_index(
        op.f("ix_state_change_events_trade_plan_id"),
        "state_change_events",
        ["trade_plan_id"],
        unique=False,
    )
    op.create_index(
        "ix_state_changes_plan_time",
        "state_change_events",
        ["trade_plan_id", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_state_changes_symbol_time",
        "state_change_events",
        ["symbol", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_state_changes_symbol_time", table_name="state_change_events")
    op.drop_index("ix_state_changes_plan_time", table_name="state_change_events")
    op.drop_index(op.f("ix_state_change_events_trade_plan_id"), table_name="state_change_events")
    op.drop_index(op.f("ix_state_change_events_timestamp"), table_name="state_change_events")
    op.drop_index(op.f("ix_state_change_events_symbol"), table_name="state_change_events")
    op.drop_index(op.f("ix_state_change_events_event_type"), table_name="state_change_events")
    op.drop_table("state_change_events")
    op.drop_index(
        op.f("ix_coach_state_cursors_trade_plan_id"), table_name="coach_state_cursors"
    )
    op.drop_table("coach_state_cursors")
