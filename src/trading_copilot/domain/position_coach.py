from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvidenceStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    MISSING = "MISSING"
    INDETERMINATE = "INDETERMINATE"


class RiskPolicyStatus(StrEnum):
    PASS = "PASS"
    BREACH = "BREACH"
    INDETERMINATE = "INDETERMINATE"


class CoachExecutionState(StrEnum):
    HOLD = "HOLD"
    ADD = "ADD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    INVALIDATE = "INVALIDATE"


class CoachPosition(BaseModel):
    quantity: Decimal
    average_entry: Decimal
    mark_price: Decimal | None = None
    unrealized_pnl_usdt: Decimal | None = None
    planned_open_risk_usdt: Decimal | None = None
    open_position_r: Decimal | None = None
    lifecycle_history_complete: bool
    lifecycle_confidence: EvidenceStatus


class CoachPlan(BaseModel):
    status: EvidenceStatus
    trade_plan_id: str | None = None
    setup_type: str | None = None
    lifecycle_status: str | None = None
    thesis: str | None = None
    hard_invalidation: Decimal | None = None
    thesis_warning: Decimal | None = None
    correlation_group: str | None = None
    targets: list[dict[str, Any]] = Field(default_factory=list)
    stop_provenance: str = "unknown"


class CoachMarketContext(BaseModel):
    status: EvidenceStatus
    regime_4h: str | None = None
    context_1h: str | None = None
    ema_12: Decimal | None = None
    ema_21: Decimal | None = None
    stoch_rsi: dict[str, Decimal | None] = Field(default_factory=dict)
    location: str
    location_status: EvidenceStatus
    playbook_state: str | None = None
    playbook_conditions: list[dict[str, Any]] = Field(default_factory=list)
    reaction: str | None = None
    reaction_status: EvidenceStatus
    order_flow: dict[str, Any] = Field(default_factory=dict)
    funding: Decimal | None = None
    open_interest: Decimal | None = None
    timestamp_ms: int | None = None


class CoachRisk(BaseModel):
    account_equity: Decimal
    position_structural_risk_usdt: Decimal | None = None
    position_risk_pct: Decimal | None = None
    correlation_group: str
    known_group_risk_usdt: Decimal
    group_risk_pct: Decimal | None = None
    max_risk_pct: Decimal | None = None
    policy_status: RiskPolicyStatus
    provenance: dict[str, str] = Field(default_factory=dict)
    incomplete_symbols: list[str] = Field(default_factory=list)


class CoachExecution(BaseModel):
    state: CoachExecutionState
    add_allowed: bool
    evidence_present: list[str] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_conditions: list[str] = Field(default_factory=list)
    invalidation: Decimal | None = None
    condition_evidence: list[str] = Field(default_factory=list)


class PositionCoach(BaseModel):
    symbol: str
    timestamp: datetime
    side: str
    position: CoachPosition
    plan: CoachPlan
    market_context: CoachMarketContext
    risk: CoachRisk
    execution: CoachExecution
    confidence: EvidenceStatus
