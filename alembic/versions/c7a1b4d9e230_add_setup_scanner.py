"""add setup scanner

Revision ID: c7a1b4d9e230
Revises: b6f0a2d5c1e7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7a1b4d9e230"
down_revision: str | Sequence[str] | None = "b6f0a2d5c1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fail_on_duplicate_watched_setups() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT symbol, setup_type, COUNT(*) AS row_count
            FROM watched_setups
            GROUP BY symbol, setup_type
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot add scanner uniqueness: duplicate watched_setups rows exist for "
            f"symbol={duplicate.symbol!r}, setup_type={duplicate.setup_type!r}"
        )


def upgrade() -> None:
    _fail_on_duplicate_watched_setups()
    op.create_table(
        "scanner_watchlist",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled_playbooks", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "approach_tolerance_bps",
            sa.Numeric(12, 4),
            nullable=False,
            server_default="50",
        ),
        sa.Column(
            "retest_tolerance_bps",
            sa.Numeric(12, 4),
            nullable=False,
            server_default="25",
        ),
        sa.Column("acceptance_bars", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "acceptance_bars >= 1", name="ck_scanner_watchlist_acceptance_bars"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", name="uq_scanner_watchlist_symbol"),
    )
    op.create_index(op.f("ix_scanner_watchlist_symbol"), "scanner_watchlist", ["symbol"])

    with op.batch_alter_table("watched_setups") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("last_evaluated_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.create_unique_constraint(
            "uq_watched_setups_symbol_type", ["symbol", "setup_type"]
        )

    op.create_table(
        "scanner_transitions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("watched_setup_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("setup_type", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_before", sa.JSON(), nullable=False),
        sa.Column("state_after", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["watched_setup_id"], ["watched_setups.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "watched_setup_id", "version", name="uq_scanner_transition_version"
        ),
    )
    op.create_index(
        "ix_scanner_transition_symbol_time", "scanner_transitions", ["symbol", "timestamp"]
    )
    op.create_index(
        "ix_scanner_transition_type_time",
        "scanner_transitions",
        ["setup_type", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_scanner_transition_type_time", table_name="scanner_transitions")
    op.drop_index("ix_scanner_transition_symbol_time", table_name="scanner_transitions")
    op.drop_table("scanner_transitions")

    with op.batch_alter_table("watched_setups") as batch:
        batch.drop_constraint("uq_watched_setups_symbol_type", type_="unique")
        batch.drop_column("updated_at")
        batch.drop_column("last_evaluated_at")
        batch.drop_column("version")

    op.drop_index(op.f("ix_scanner_watchlist_symbol"), table_name="scanner_watchlist")
    op.drop_table("scanner_watchlist")
