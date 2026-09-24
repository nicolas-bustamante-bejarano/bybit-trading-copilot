"""add persistent trigger attempts

Revision ID: e4f8a9c2d713
Revises: c7a1b4d9e230
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4f8a9c2d713"
down_revision: str | Sequence[str] | None = "c7a1b4d9e230"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trigger_attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("watched_setup_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("setup_type", sa.String(64), nullable=False),
        sa.Column("arm_key", sa.String(160), nullable=False),
        sa.Column("arm_source", sa.String(48), nullable=False),
        sa.Column("arm_transition_id", sa.String(36)),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_level", sa.Numeric(28, 12), nullable=False),
        sa.Column("reference_source", sa.String(48), nullable=False),
        sa.Column("reference_metadata", sa.JSON(), nullable=False),
        sa.Column("retest_tolerance_bps", sa.Numeric(12, 4), nullable=False),
        sa.Column("failure_tolerance_bps", sa.Numeric(12, 4), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("first_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["watched_setup_id"], ["watched_setups.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "watched_setup_id", "arm_key", name="uq_trigger_attempt_arm"
        ),
    )
    op.create_index(
        "ix_trigger_attempts_watched_setup_id", "trigger_attempts", ["watched_setup_id"]
    )
    op.create_index("ix_trigger_attempts_symbol", "trigger_attempts", ["symbol"])
    op.create_index(
        "ix_trigger_attempts_arm_transition_id", "trigger_attempts", ["arm_transition_id"]
    )
    op.create_index(
        "ix_trigger_attempt_symbol_type", "trigger_attempts", ["symbol", "setup_type"]
    )
    op.create_index(
        "ix_trigger_attempt_last_evaluated", "trigger_attempts", ["last_evaluated_at"]
    )

    op.create_table(
        "trigger_transitions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("trigger_attempt_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("setup_type", sa.String(64), nullable=False),
        sa.Column("from_state", sa.String(32)),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_before", sa.JSON(), nullable=False),
        sa.Column("result_after", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trigger_attempt_id"], ["trigger_attempts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trigger_attempt_id", "version", name="uq_trigger_transition_version"
        ),
    )
    op.create_index(
        "ix_trigger_transitions_trigger_attempt_id",
        "trigger_transitions",
        ["trigger_attempt_id"],
    )
    op.create_index(
        "ix_trigger_transitions_symbol", "trigger_transitions", ["symbol"]
    )
    op.create_index(
        "ix_trigger_transition_symbol_time",
        "trigger_transitions",
        ["symbol", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_trigger_transition_symbol_time", table_name="trigger_transitions")
    op.drop_index("ix_trigger_transitions_symbol", table_name="trigger_transitions")
    op.drop_index(
        "ix_trigger_transitions_trigger_attempt_id", table_name="trigger_transitions"
    )
    op.drop_table("trigger_transitions")

    op.drop_index("ix_trigger_attempt_last_evaluated", table_name="trigger_attempts")
    op.drop_index("ix_trigger_attempt_symbol_type", table_name="trigger_attempts")
    op.drop_index("ix_trigger_attempts_arm_transition_id", table_name="trigger_attempts")
    op.drop_index("ix_trigger_attempts_symbol", table_name="trigger_attempts")
    op.drop_index("ix_trigger_attempts_watched_setup_id", table_name="trigger_attempts")
    op.drop_table("trigger_attempts")
