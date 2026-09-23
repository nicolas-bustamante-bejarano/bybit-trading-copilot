"""add persisted chart structures

Revision ID: b6f0a2d5c1e7
Revises: 9d3f6c8a12b4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6f0a2d5c1e7"
down_revision: str | Sequence[str] | None = "9d3f6c8a12b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chart_structures",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("structure_type", sa.String(32), nullable=False),
        sa.Column("label", sa.String(128)),
        sa.Column("lower_price", sa.Numeric(28, 12)),
        sa.Column("upper_price", sa.Numeric(28, 12)),
        sa.Column("anchor_one_time", sa.Integer()),
        sa.Column("anchor_one_price", sa.Numeric(28, 12)),
        sa.Column("anchor_two_time", sa.Integer()),
        sa.Column("anchor_two_price", sa.Numeric(28, 12)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chart_structures_symbol"), "chart_structures", ["symbol"])
    op.create_index(op.f("ix_chart_structures_timeframe"), "chart_structures", ["timeframe"])
    op.create_index(op.f("ix_chart_structures_active"), "chart_structures", ["active"])


def downgrade() -> None:
    op.drop_index(op.f("ix_chart_structures_active"), table_name="chart_structures")
    op.drop_index(op.f("ix_chart_structures_timeframe"), table_name="chart_structures")
    op.drop_index(op.f("ix_chart_structures_symbol"), table_name="chart_structures")
    op.drop_table("chart_structures")
