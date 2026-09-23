from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import ScannerStatus

POST_ACCEPTANCE_STATUSES = {
    ScannerStatus.BREAKOUT_ACCEPTED,
    ScannerStatus.RETEST_PENDING,
    ScannerStatus.TRIGGER_ARMED,
}

NEXT_CONDITIONS = {
    ScannerStatus.WATCH: "approach breakout structure",
    ScannerStatus.APPROACHING_BREAKOUT: "trade beyond structure",
    ScannerStatus.BREAKOUT_ATTEMPT: "await completed 1H close beyond structure",
    ScannerStatus.ACCEPTANCE_PENDING: "await additional completed 1H acceptance",
    ScannerStatus.BREAKOUT_ACCEPTED: "await future retest",
    ScannerStatus.RETEST_PENDING: "await return to breakout level",
    ScannerStatus.TRIGGER_ARMED: "await lower-timeframe trigger engine",
}


@dataclass(frozen=True)
class MacroScannerResult:
    status: ScannerStatus
    side: str
    breakout_level: float
    structure_id: str
    distance_bps: float
    qualifying_close_count: int
    required_acceptance_bars: int
    evaluated_at: datetime
    accepted_at: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    structure: dict[str, Any] = field(default_factory=dict)
    blocking_reasons: list[str] = field(default_factory=list)
    next_conditions: list[str] = field(default_factory=list)


def _distance_bps(current_price: float, breakout_level: float) -> float:
    return abs(current_price - breakout_level) / breakout_level * 10_000


def _consecutive_qualifying_closes(
    completed_1h_closes: Sequence[float], side: Side, breakout_level: float
) -> int:
    count = 0
    for close in reversed(completed_1h_closes):
        qualifies = close > breakout_level if side == Side.LONG else close < breakout_level
        if not qualifies:
            break
        count += 1
    return count


def _previous_acceptance_matches(
    *,
    previous_status: ScannerStatus | str | None,
    previous_state: Mapping[str, Any],
    structure_id: str,
    breakout_level: float,
    side: Side,
) -> bool:
    try:
        status = ScannerStatus(previous_status) if previous_status is not None else None
    except ValueError:
        return False
    if status not in POST_ACCEPTANCE_STATUSES:
        return False

    accepted_at = previous_state.get("accepted_at")
    accepted_count = previous_state.get("accepted_close_count")
    accepted_bars = previous_state.get("acceptance_bars")
    accepted_level = previous_state.get("accepted_breakout_level")
    return (
        isinstance(accepted_at, (str, datetime))
        and bool(accepted_at)
        and isinstance(accepted_count, int)
        and not isinstance(accepted_count, bool)
        and isinstance(accepted_bars, int)
        and not isinstance(accepted_bars, bool)
        and accepted_bars >= 1
        and accepted_count >= accepted_bars
        and previous_state.get("accepted_structure_id") == structure_id
        and previous_state.get("accepted_side") == side.value.upper()
        and isinstance(accepted_level, (int, float))
        and not isinstance(accepted_level, bool)
        and float(accepted_level) == breakout_level
    )


def evaluate_macro_breakout(
    *,
    side: Side | str,
    current_price: float,
    breakout_level: float,
    approach_tolerance_bps: float,
    retest_tolerance_bps: float,
    acceptance_bars: int,
    completed_1h_closes: Sequence[float],
    previous_status: ScannerStatus | str | None,
    previous_state: Mapping[str, Any] | None,
    structure_id: str,
    structure_metadata: Mapping[str, Any] | None,
    evaluated_at: datetime,
) -> MacroScannerResult:
    """Evaluate macro lifecycle using completed 1H closes only.

    The caller is responsible for excluding the current partial bar. On first acceptance,
    ``evaluated_at`` is serialized as ``accepted_at`` so the acceptance transition has a
    deterministic timestamp even when completed-close timestamps are not supplied.
    """
    normalized_side = Side(str(side).lower())
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    if breakout_level <= 0:
        raise ValueError("breakout_level must be positive")
    if acceptance_bars < 1:
        raise ValueError("acceptance_bars must be at least 1")
    if approach_tolerance_bps < 0 or retest_tolerance_bps < 0:
        raise ValueError("tolerances must be non-negative")
    if not structure_id:
        raise ValueError("structure_id is required")
    if previous_state is not None and not isinstance(previous_state, Mapping):
        raise ValueError("previous_state must be a mapping")
    if structure_metadata is not None and not isinstance(structure_metadata, Mapping):
        raise ValueError("structure_metadata must be a mapping")
    if isinstance(completed_1h_closes, (str, bytes)):
        raise TypeError("completed_1h_closes must be a sequence of prices")
    closes = list(completed_1h_closes)
    if any(isinstance(close, bool) or not isinstance(close, (int, float)) or close <= 0 for close in closes):
        raise ValueError("completed_1h_closes must contain positive prices")

    prior = dict(previous_state or {})
    distance_bps = _distance_bps(current_price, breakout_level)
    qualifying_count = _consecutive_qualifying_closes(closes, normalized_side, breakout_level)
    memory_matches = _previous_acceptance_matches(
        previous_status=previous_status,
        previous_state=prior,
        structure_id=structure_id,
        breakout_level=breakout_level,
        side=normalized_side,
    )

    if memory_matches:
        status = (
            ScannerStatus.TRIGGER_ARMED
            if distance_bps <= retest_tolerance_bps
            else ScannerStatus.RETEST_PENDING
        )
        state = prior
        accepted_at = prior["accepted_at"]
        if isinstance(accepted_at, datetime):
            accepted_at = accepted_at.isoformat()
    elif qualifying_count >= acceptance_bars:
        status = ScannerStatus.BREAKOUT_ACCEPTED
        accepted_at = evaluated_at.isoformat()
        state = {
            "accepted_at": accepted_at,
            "accepted_breakout_level": breakout_level,
            "accepted_structure_id": structure_id,
            "accepted_close_count": qualifying_count,
            "acceptance_bars": acceptance_bars,
            "accepted_side": normalized_side.value.upper(),
        }
    elif qualifying_count > 0:
        status = ScannerStatus.ACCEPTANCE_PENDING
        accepted_at = None
        state = {}
    else:
        beyond = (
            current_price > breakout_level
            if normalized_side == Side.LONG
            else current_price < breakout_level
        )
        approaching_from_valid_side = (
            current_price <= breakout_level
            if normalized_side == Side.LONG
            else current_price >= breakout_level
        )
        if beyond:
            status = ScannerStatus.BREAKOUT_ATTEMPT
        elif approaching_from_valid_side and distance_bps <= approach_tolerance_bps:
            status = ScannerStatus.APPROACHING_BREAKOUT
        else:
            status = ScannerStatus.WATCH
        accepted_at = None
        state = {}

    return MacroScannerResult(
        status=status,
        side=normalized_side.value.upper(),
        breakout_level=breakout_level,
        structure_id=structure_id,
        distance_bps=distance_bps,
        qualifying_close_count=qualifying_count,
        required_acceptance_bars=acceptance_bars,
        evaluated_at=evaluated_at,
        accepted_at=accepted_at,
        state=state,
        structure=dict(structure_metadata or {}),
        next_conditions=[NEXT_CONDITIONS[status]],
    )
