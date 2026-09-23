from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Action(str, Enum):
    ENTRY = "ENTRY"
    PROBE = "PROBE"
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    INVALIDATE = "INVALIDATE"
    WAIT = "WAIT"


class Target(BaseModel):
    price: Decimal
    reduction_percent: Decimal = Field(gt=0, le=1)
    ordering: int = Field(ge=0)


class RuleInput(BaseModel):
    action: Action
    rule_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str
    ordering: int = 0


class TradePlanCreate(BaseModel):
    symbol: str
    side: Side
    setup_type: str
    thesis: str
    lifecycle_status: str = "DRAFT"
    hard_invalidation: Decimal
    thesis_warning: Decimal | None = None
    max_risk_percent: Decimal = Field(gt=0, le=1)
    max_risk_value: Decimal | None = Field(default=None, ge=0)
    correlation_group: str | None = None
    entry_probe_plan: dict[str, Any] = Field(default_factory=dict)
    add_conditions: list[dict[str, Any]] = Field(default_factory=list)
    target_ladder: list[Target] = Field(default_factory=list)
    execution_rules: list[RuleInput] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_targets(self):
        if sum((target.reduction_percent for target in self.target_ladder), Decimal()) > Decimal(1):
            raise ValueError("target reductions cannot exceed 100%")
        return self


class TradePlanPatch(BaseModel):
    symbol: str | None = None
    side: Side | None = None
    setup_type: str | None = None
    thesis: str | None = None
    lifecycle_status: str | None = None
    hard_invalidation: Decimal | None = None
    thesis_warning: Decimal | None = None
    max_risk_percent: Decimal | None = Field(default=None, gt=0, le=1)
    max_risk_value: Decimal | None = Field(default=None, ge=0)
    correlation_group: str | None = None
    entry_probe_plan: dict[str, Any] | None = None
    add_conditions: list[dict[str, Any]] | None = None
    target_ladder: list[Target] | None = None
    notes: str | None = None


class SnapshotCreate(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    symbol: str
    price: Decimal
    action_considered: Action
    action_taken: Action | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    evidence_present: list[str] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    confirmation_conditions: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None
    data_confidence: dict[str, Any] = Field(default_factory=dict)


class ExecutionEventCreate(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    symbol: str
    event_type: Action
    bybit_execution_id: str | None = None
    bybit_order_id: str | None = None
    quantity: Decimal | None = Field(default=None, ge=0)
    price: Decimal | None = None
    position_before: dict[str, Any] = Field(default_factory=dict)
    position_after: dict[str, Any] = Field(default_factory=dict)
    risk_before: dict[str, Any] = Field(default_factory=dict)
    risk_after: dict[str, Any] = Field(default_factory=dict)
    planned: bool | None = None
    confirmation_satisfied: bool | None = None
    risk_policy_satisfied: bool | None = None
    decision_snapshot_id: str | None = None
    confidence_status: str = "INDETERMINATE"


class ReviewCreate(BaseModel):
    realized_r: Decimal | None = None
    facts: dict[str, bool | None] = Field(default_factory=dict)
    deviations: list[str] = Field(default_factory=list)
    lifecycle_history_complete: bool | None = None
    data_confidence: dict[str, Any] = Field(default_factory=dict)


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
