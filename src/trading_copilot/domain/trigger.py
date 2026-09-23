from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import ScannerSetupType


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
