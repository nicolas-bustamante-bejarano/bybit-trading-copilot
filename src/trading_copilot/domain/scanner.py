from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ScannerStatus(StrEnum):
    IGNORE = "IGNORE"; WATCH = "WATCH"; APPROACHING_LOCATION = "APPROACHING_LOCATION"; AT_LOCATION = "AT_LOCATION"; REACTION_DEVELOPING = "REACTION_DEVELOPING"; TRIGGER_ARMED = "TRIGGER_ARMED"; APPROACHING_BREAKOUT = "APPROACHING_BREAKOUT"; BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"; ACCEPTANCE_PENDING = "ACCEPTANCE_PENDING"; BREAKOUT_ACCEPTED = "BREAKOUT_ACCEPTED"; RETEST_PENDING = "RETEST_PENDING"


class ScannerSetupType(StrEnum):
    TREND_PULLBACK_LONG = "TREND_PULLBACK_LONG"; TREND_PULLBACK_SHORT = "TREND_PULLBACK_SHORT"; RANGE_LONG = "RANGE_LONG"; RANGE_SHORT = "RANGE_SHORT"; MACRO_BREAKOUT_LONG = "MACRO_BREAKOUT_LONG"; MACRO_BREAKOUT_SHORT = "MACRO_BREAKOUT_SHORT"


class ScannerDataStatus(StrEnum):
    CONFIRMED = "CONFIRMED"; PARTIAL = "PARTIAL"; INDETERMINATE = "INDETERMINATE"


class ScannerResult(BaseModel):
    symbol: str; setup_type: ScannerSetupType; side: str; status: ScannerStatus; price: float; evaluated_at: datetime
    context: dict[str, Any] = {}; location: dict[str, Any] = {}; reaction: dict[str, Any] = {}; structure: dict[str, Any] = {}
    conditions: list[dict[str, Any]] = []; blocking_reasons: list[str] = []; next_conditions: list[str] = []
    data_status: ScannerDataStatus = ScannerDataStatus.CONFIRMED; linked_trade_plan_id: str | None = None


def distance_to_interval_bps(price: float, low: float, high: float) -> float:
    if low <= price <= high: return 0.0
    return abs(price - (low if price < low else high)) / price * 10_000


def distance_to_level_bps(price: float, level: float) -> float: return abs(price - level) / price * 10_000


def project_trendline(a_time: int, a_price: float, b_time: int, b_price: float, timestamp: int) -> float | None:
    if b_time == a_time: return None
    return a_price + (b_price - a_price) * (timestamp - a_time) / (b_time - a_time)


def macro_status(*, side: str, price: float, level: float, completed_closes: list[float], acceptance_bars: int = 2, approach_bps: float = 50, retest_bps: float = 25, prior: dict[str, Any] | None = None) -> tuple[ScannerStatus, dict[str, Any]]:
    long = side.upper() == "LONG"; accepted = bool(prior and prior.get("accepted_at")); qualifies = [(close > level) if long else (close < level) for close in completed_closes]
    consecutive = 0
    for value in reversed(qualifies):
        if not value: break
        consecutive += 1
    state = dict(prior or {})
    if accepted or consecutive >= acceptance_bars:
        state.update({"accepted_at": state.get("accepted_at", "accepted"), "breakout_level": level, "accepted_close_count": consecutive})
        return (ScannerStatus.TRIGGER_ARMED if distance_to_level_bps(price, level) <= retest_bps else ScannerStatus.RETEST_PENDING, state)
    beyond = price > level if long else price < level
    if consecutive: return ScannerStatus.ACCEPTANCE_PENDING, state
    if beyond: return ScannerStatus.BREAKOUT_ATTEMPT, state
    return (ScannerStatus.APPROACHING_BREAKOUT if distance_to_level_bps(price, level) <= approach_bps else ScannerStatus.WATCH, state)
