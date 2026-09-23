from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ScannerStatus(StrEnum):
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    APPROACHING_LOCATION = "APPROACHING_LOCATION"
    AT_LOCATION = "AT_LOCATION"
    REACTION_DEVELOPING = "REACTION_DEVELOPING"
    TRIGGER_ARMED = "TRIGGER_ARMED"
    APPROACHING_BREAKOUT = "APPROACHING_BREAKOUT"
    BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"
    ACCEPTANCE_PENDING = "ACCEPTANCE_PENDING"
    BREAKOUT_ACCEPTED = "BREAKOUT_ACCEPTED"
    RETEST_PENDING = "RETEST_PENDING"


class ScannerSetupType(StrEnum):
    TREND_PULLBACK_LONG = "TREND_PULLBACK_LONG"
    TREND_PULLBACK_SHORT = "TREND_PULLBACK_SHORT"
    RANGE_LONG = "RANGE_LONG"
    RANGE_SHORT = "RANGE_SHORT"
    MACRO_BREAKOUT_LONG = "MACRO_BREAKOUT_LONG"
    MACRO_BREAKOUT_SHORT = "MACRO_BREAKOUT_SHORT"


class ScannerDataStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    INDETERMINATE = "INDETERMINATE"


class ScannerResult(BaseModel):
    symbol: str
    setup_type: ScannerSetupType
    side: str
    status: ScannerStatus
    price: float
    evaluated_at: datetime
    context: dict[str, Any] = Field(default_factory=dict)
    location: dict[str, Any] = Field(default_factory=dict)
    reaction: dict[str, Any] = Field(default_factory=dict)
    structure: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    next_conditions: list[str] = Field(default_factory=list)
    data_status: ScannerDataStatus = ScannerDataStatus.CONFIRMED
    linked_trade_plan_id: str | None = None
