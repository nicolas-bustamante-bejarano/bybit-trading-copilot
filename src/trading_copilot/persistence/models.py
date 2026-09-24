from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
    __table_args__ = (UniqueConstraint("symbol", "setup_type", name="uq_watched_setups_symbol_type"),)
    setup_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScannerWatchlistRow(SymbolMixin, Base):
    __tablename__ = "scanner_watchlist"
    __table_args__ = (
        UniqueConstraint("symbol", name="uq_scanner_watchlist_symbol"),
        CheckConstraint("acceptance_bars >= 1", name="ck_scanner_watchlist_acceptance_bars"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled_playbooks: Mapped[list] = mapped_column(JSON, default=list)
    approach_tolerance_bps: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=50)
    retest_tolerance_bps: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=25)
    acceptance_bars: Mapped[int] = mapped_column(default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScannerTransitionRow(SymbolMixin, Base):
    __tablename__ = "scanner_transitions"
    __table_args__ = (
        UniqueConstraint("watched_setup_id", "version", name="uq_scanner_transition_version"),
        Index("ix_scanner_transition_symbol_time", "symbol", "timestamp"),
        Index("ix_scanner_transition_type_time", "setup_type", "timestamp"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    watched_setup_id: Mapped[str] = mapped_column(
        ForeignKey("watched_setups.id", ondelete="CASCADE")
    )
    setup_type: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state_before: Mapped[dict] = mapped_column(JSON, default=dict)
    state_after: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column()


class TriggerAttemptRow(SymbolMixin, Base):
    __tablename__ = "trigger_attempts"
    __table_args__ = (
        UniqueConstraint("watched_setup_id", "arm_key", name="uq_trigger_attempt_arm"),
        Index("ix_trigger_attempt_symbol_type", "symbol", "setup_type"),
        Index("ix_trigger_attempt_last_evaluated", "last_evaluated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    watched_setup_id: Mapped[str] = mapped_column(
        ForeignKey("watched_setups.id", ondelete="CASCADE"), index=True
    )
    setup_type: Mapped[str] = mapped_column(String(64))
    arm_key: Mapped[str] = mapped_column(String(160))
    arm_source: Mapped[str] = mapped_column(String(48))
    # Kept as an indexed durable identifier rather than an FK so trigger audit history
    # does not depend on scanner-transition retention policy.
    arm_transition_id: Mapped[str | None] = mapped_column(String(36), index=True)
    armed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reference_level: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    reference_source: Mapped[str] = mapped_column(String(48))
    reference_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    retest_tolerance_bps: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    failure_tolerance_bps: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    state: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)
    first_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TriggerTransitionRow(SymbolMixin, Base):
    __tablename__ = "trigger_transitions"
    __table_args__ = (
        UniqueConstraint(
            "trigger_attempt_id", "version", name="uq_trigger_transition_version"
        ),
        Index("ix_trigger_transition_symbol_time", "symbol", "timestamp"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    trigger_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("trigger_attempts.id", ondelete="CASCADE"), index=True
    )
    setup_type: Mapped[str] = mapped_column(String(64))
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_before: Mapped[dict] = mapped_column(JSON)
    result_after: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column()


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


class ChartStructureRow(SymbolMixin, Base):
    __tablename__ = "chart_structures"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    structure_type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str | None] = mapped_column(String(128))
    lower_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    upper_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    anchor_one_time: Mapped[int | None] = mapped_column()
    anchor_one_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    anchor_two_time: Mapped[int | None] = mapped_column()
    anchor_two_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CoachStateCursorRow(SymbolMixin, Base):
    __tablename__ = "coach_state_cursors"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    position_open: Mapped[bool] = mapped_column(Boolean)
    trade_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("trade_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    state: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StateChangeEventRow(SymbolMixin, Base):
    __tablename__ = "state_change_events"
    __table_args__ = (
        UniqueConstraint("symbol", "to_version", name="uq_state_change_symbol_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trade_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("trade_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    importance: Mapped[str] = mapped_column(String(16))
    changes: Mapped[list] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    state_before: Mapped[dict] = mapped_column(JSON)
    state_after: Mapped[dict] = mapped_column(JSON)
    from_version: Mapped[int] = mapped_column()
    to_version: Mapped[int] = mapped_column()
    confidence_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_snapshots_plan_time", DecisionSnapshotRow.trade_plan_id, DecisionSnapshotRow.timestamp)
Index("ix_events_plan_time", ExecutionEventRow.trade_plan_id, ExecutionEventRow.timestamp)
Index("ix_state_changes_symbol_time", StateChangeEventRow.symbol, StateChangeEventRow.timestamp)
Index(
    "ix_state_changes_plan_time", StateChangeEventRow.trade_plan_id, StateChangeEventRow.timestamp
)
