from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


SCANNER_PLAYBOOK_FAMILIES = ("TREND_PULLBACK", "RANGE", "MACRO_BREAKOUT")
SCANNER_PLAYBOOK_ORDER = (*SCANNER_PLAYBOOK_FAMILIES, *(item.value for item in ScannerSetupType))
SCANNER_PLAYBOOK_NAMES = frozenset(SCANNER_PLAYBOOK_ORDER)


def normalize_enabled_playbooks(values: list[str]) -> list[str]:
    normalized = {value.strip().upper() for value in values}
    unknown = sorted(normalized - SCANNER_PLAYBOOK_NAMES)
    if unknown:
        raise ValueError(f"unsupported scanner playbook: {unknown[0]}")
    return [name for name in SCANNER_PLAYBOOK_ORDER if name in normalized]


class ScannerWatchlistCreate(BaseModel):
    symbol: str
    enabled: bool = True
    enabled_playbooks: list[str] = Field(default_factory=list)
    approach_tolerance_bps: Decimal = Field(default=Decimal(50), ge=0)
    retest_tolerance_bps: Decimal = Field(default=Decimal(25), ge=0)
    acceptance_bars: int = Field(default=2, ge=1)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized

    @field_validator("enabled_playbooks")
    @classmethod
    def validate_playbooks(cls, value: list[str]) -> list[str]:
        return normalize_enabled_playbooks(value)


class ScannerWatchlistPatch(BaseModel):
    enabled: bool | None = None
    enabled_playbooks: list[str] | None = None
    approach_tolerance_bps: Decimal | None = Field(default=None, ge=0)
    retest_tolerance_bps: Decimal | None = Field(default=None, ge=0)
    acceptance_bars: int | None = Field(default=None, ge=1)

    @field_validator("enabled_playbooks")
    @classmethod
    def validate_playbooks(cls, value: list[str] | None) -> list[str] | None:
        return normalize_enabled_playbooks(value) if value is not None else None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> ScannerWatchlistPatch:
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must not be null")
        return self


class ScannerWatchlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    symbol: str
    enabled: bool
    enabled_playbooks: list[str]
    approach_tolerance_bps: float
    retest_tolerance_bps: float
    acceptance_bars: int
    created_at: datetime
    updated_at: datetime


class ScannerSetupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    symbol: str
    setup_type: ScannerSetupType
    status: ScannerStatus
    state: dict[str, Any]
    version: int
    trade_plan_id: str | None
    last_evaluated_at: datetime | None
    updated_at: datetime
    created_at: datetime


class ScannerTransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    watched_setup_id: str
    symbol: str
    setup_type: ScannerSetupType
    from_status: ScannerStatus | None
    to_status: ScannerStatus
    timestamp: datetime
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    version: int


class ScannerMonitorStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    running: bool
    interval_seconds: float
    last_cycle_started_at: datetime | None = None
    last_cycle_completed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_cycle_duration_ms: float | None = None
    watchlist_count: int = 0
    setup_count: int = 0
    evaluated_symbols: list[str] = Field(default_factory=list)
    failed_symbols: list[str] = Field(default_factory=list)
