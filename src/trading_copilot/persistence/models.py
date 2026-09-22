from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SymbolMixin:
    symbol: Mapped[str] = mapped_column(String(32), index=True)

    @validates("symbol")
    def normalize_symbol(self, _: str, value: str) -> str:
        return value.upper()


class TradePlanRow(SymbolMixin, Base):
    __tablename__ = "trade_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    side: Mapped[str] = mapped_column(String(8))
    setup_type: Mapped[str] = mapped_column(String(64))
    thesis: Mapped[str] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    hard_invalidation: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    thesis_warning: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    max_risk_percent: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    max_risk_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    correlation_group: Mapped[str | None] = mapped_column(String(64), index=True)
    entry_probe_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    add_conditions: Mapped[list] = mapped_column(JSON, default=list)
    target_ladder: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ChildBase:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    trade_plan_id: Mapped[str] = mapped_column(
        ForeignKey("trade_plans.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OptionalPlanChildBase:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    trade_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("trade_plans.id", ondelete="SET NULL"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExecutionRuleRow(ChildBase, Base):
    __tablename__ = "execution_rules"
    action: Mapped[str] = mapped_column(String(16))
    rule_type: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text)
    ordering: Mapped[int] = mapped_column(default=0)


class DecisionSnapshotRow(SymbolMixin, ChildBase, Base):
    __tablename__ = "decision_snapshots"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    action_considered: Mapped[str] = mapped_column(String(16))
    action_taken: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_present: Mapped[list] = mapped_column(JSON, default=list)
    evidence_missing: Mapped[list] = mapped_column(JSON, default=list)
    confirmation_conditions: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    data_confidence: Mapped[dict] = mapped_column(JSON, default=dict)


class ExecutionEventRow(SymbolMixin, ChildBase, Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        UniqueConstraint("bybit_execution_id", name="uq_execution_events_bybit_execution_id"),
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(16))
    bybit_execution_id: Mapped[str | None] = mapped_column(String(128))
    bybit_order_id: Mapped[str | None] = mapped_column(String(128))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    position_before: Mapped[dict] = mapped_column(JSON, default=dict)
    position_after: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_before: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_after: Mapped[dict] = mapped_column(JSON, default=dict)
    planned: Mapped[bool | None] = mapped_column(Boolean)
    confirmation_satisfied: Mapped[bool | None] = mapped_column(Boolean)
    risk_policy_satisfied: Mapped[bool | None] = mapped_column(Boolean)
    decision_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("decision_snapshots.id"))
    confidence_status: Mapped[str] = mapped_column(String(32), default="INDETERMINATE")


class TradeReviewRow(ChildBase, Base):
    __tablename__ = "trade_reviews"
    __table_args__ = (UniqueConstraint("trade_plan_id", name="uq_trade_reviews_trade_plan_id"),)
    realized_r: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    deviations: Mapped[list] = mapped_column(JSON, default=list)
    lifecycle_history_complete: Mapped[bool | None] = mapped_column(Boolean)
    data_confidence: Mapped[dict] = mapped_column(JSON, default=dict)


class WatchedSetupRow(SymbolMixin, OptionalPlanChildBase, Base):
    __tablename__ = "watched_setups"
    setup_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class FibDefinitionRow(SymbolMixin, OptionalPlanChildBase, Base):
    __tablename__ = "fib_definitions"
    direction: Mapped[str] = mapped_column(String(8))
    swing_low: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    swing_high: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class RangeDefinitionRow(SymbolMixin, OptionalPlanChildBase, Base):
    __tablename__ = "range_definitions"
    range_low: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    range_high: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


Index("ix_snapshots_plan_time", DecisionSnapshotRow.trade_plan_id, DecisionSnapshotRow.timestamp)
Index("ix_events_plan_time", ExecutionEventRow.trade_plan_id, ExecutionEventRow.timestamp)
