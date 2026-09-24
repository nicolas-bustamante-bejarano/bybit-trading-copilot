from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import ScannerSetupType, ScannerStatus


class TriggerPattern(StrEnum):
    DEVIATION_RECLAIM = "DEVIATION_RECLAIM"
    RETEST_HOLD = "RETEST_HOLD"


class TriggerState(StrEnum):
    WAITING = "WAITING"
    DEVELOPING = "DEVELOPING"
    RECLAIMED = "RECLAIMED"
    RETEST_HELD = "RETEST_HELD"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    INDETERMINATE = "INDETERMINATE"


class TriggerArmSource(StrEnum):
    ARM_TRANSITION = "ARM_TRANSITION"
    FIRST_OBSERVATION_BASELINE = "FIRST_OBSERVATION_BASELINE"


class TriggerReferenceSource(StrEnum):
    RANGE_LOW = "RANGE_LOW"
    RANGE_HIGH = "RANGE_HIGH"
    FIB_ZONE_LOWER = "FIB_ZONE_LOWER"
    FIB_ZONE_UPPER = "FIB_ZONE_UPPER"
    ACCEPTED_BREAKOUT_LEVEL = "ACCEPTED_BREAKOUT_LEVEL"


class LowerTimeframeBar(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    start_ms: int
    end_ms: int
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_interval_and_prices(self) -> LowerTimeframeBar:
        if self.start_ms >= self.end_ms:
            raise ValueError("bar start_ms must be earlier than end_ms")
        if self.low > self.high:
            raise ValueError("bar low must not exceed high")
        if not self.low <= self.open <= self.high:
            raise ValueError("bar open must be within low/high")
        if not self.low <= self.close <= self.high:
            raise ValueError("bar close must be within low/high")
        return self


class LowerTimeframeTriggerSnapshot(BaseModel):
    symbol: str
    evaluated_at: datetime
    bars_5m: list[LowerTimeframeBar]
    bars_15m: list[LowerTimeframeBar]
    reaction_state: str | None = None
    data_status: str
    diagnostics: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def normalize_snapshot_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be empty")
        return symbol

    @field_validator("evaluated_at")
    @classmethod
    def require_aware_snapshot_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return value


class TriggerEvaluationRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: str
    setup_type: ScannerSetupType
    side: Side
    reference_level: float = Field(gt=0)
    bars_15m: list[LowerTimeframeBar] = Field(default_factory=list)
    bars_5m: list[LowerTimeframeBar] = Field(default_factory=list)
    armed_at: datetime
    evaluated_at: datetime
    retest_tolerance_bps: float = Field(default=25, ge=0)
    failure_tolerance_bps: float = Field(default=25, ge=0)
    reaction_state: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be empty")
        return symbol

    @field_validator("armed_at", "evaluated_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trigger timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_setup_side(self) -> TriggerEvaluationRequest:
        if self.armed_at > self.evaluated_at:
            raise ValueError("armed_at must not be later than evaluated_at")
        expected = Side.LONG if self.setup_type.value.endswith("_LONG") else Side.SHORT
        if self.side != expected:
            raise ValueError("side must match setup_type")
        return self


class TriggerResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: str
    setup_type: ScannerSetupType
    side: Side
    pattern: TriggerPattern
    state: TriggerState
    reference_level: float
    armed_at: datetime
    evaluated_at: datetime
    trigger_confirmed: bool
    anchor_bar_end_ms: int | None = None
    confirmation_bar_end_ms: int | None = None
    local_15m_acceptance: bool | None = None
    reaction_state: str | None = None
    reaction_supportive: bool | None = None
    anchor_high: float | None = None
    anchor_low: float | None = None
    anchor_close: float | None = None
    confirmation_close: float | None = None
    evidence_present: list[str] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    next_conditions: list[str] = Field(default_factory=list)


class TriggerMonitorStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    running: bool
    interval_seconds: float
    last_cycle_started_at: datetime | None = None
    last_cycle_completed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_cycle_duration_ms: float | None = None
    armed_candidate_count: int = 0
    eligible_candidate_count: int = 0
    evaluation_count: int = 0
    persisted_count: int = 0
    confirmed_count: int = 0
    terminal_count: int = 0
    evaluated_symbols: list[str] = Field(default_factory=list)
    failed_symbols: list[str] = Field(default_factory=list)


class TriggerAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    watched_setup_id: str
    symbol: str
    setup_type: ScannerSetupType
    arm_key: str
    arm_source: TriggerArmSource
    arm_transition_id: str | None
    armed_at: datetime
    reference_level: float
    reference_source: TriggerReferenceSource
    reference_metadata: dict[str, Any]
    retest_tolerance_bps: float
    failure_tolerance_bps: float
    state: TriggerState
    result: TriggerResult
    version: int
    first_evaluated_at: datetime
    last_evaluated_at: datetime
    created_at: datetime
    updated_at: datetime


class TriggerTransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trigger_attempt_id: str
    symbol: str
    setup_type: ScannerSetupType
    from_state: TriggerState | None
    to_state: TriggerState
    timestamp: datetime
    result_before: TriggerResult
    result_after: TriggerResult
    version: int


class CurrentTriggerResponse(BaseModel):
    watched_setup_id: str
    symbol: str
    setup_type: ScannerSetupType
    scanner_status: ScannerStatus
    eligible: bool
    blocking_reason: str | None = None
    arm_key: str | None = None
    arm_source: TriggerArmSource | None = None
    armed_at: datetime | None = None
    reference_level: float | None = None
    reference_source: TriggerReferenceSource | None = None
    attempt: TriggerAttemptResponse | None = None
    terminal: bool = False
