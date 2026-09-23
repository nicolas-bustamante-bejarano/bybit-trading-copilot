from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Candle:
    start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_bybit(cls, row: list[str]) -> "Candle":
        return cls(
            start_ms=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )


def parse_klines(rows: list[list[str]]) -> list[Candle]:
    return sorted((Candle.from_bybit(row) for row in rows), key=lambda candle: candle.start_ms)


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = (item * alpha) + (value * (1 - alpha))
    return value


def regime_at_entry(
    candles: list[Candle],
    entry_timestamp_ms: int,
    interval_ms: int,
    side: str,
) -> dict[str, Any]:
    closed = [
        candle
        for candle in candles
        if candle.start_ms + interval_ms <= entry_timestamp_ms
    ]
    closes = [candle.close for candle in closed]
    ema12 = _ema(closes, 12)
    ema21 = _ema(closes, 21)
    if ema12 is None or ema21 is None:
        return {
            "status": "UNKNOWN",
            "aligned": None,
            "ema12": ema12,
            "ema21": ema21,
            "closed_bars_used": len(closed),
        }

    normalized_side = side.upper()
    aligned = ema12 >= ema21 if normalized_side == "LONG" else ema12 <= ema21
    status = "BULLISH" if ema12 >= ema21 else "BEARISH"
    return {
        "status": status,
        "aligned": aligned,
        "ema12": ema12,
        "ema21": ema21,
        "closed_bars_used": len(closed),
    }


def excursion_metrics(
    candles: list[Candle],
    *,
    side: str,
    entry_price: float,
    invalidation: float,
    entry_timestamp_ms: int,
    interval_ms: int,
    end_timestamp_ms: int | None = None,
) -> dict[str, Any]:
    risk_per_unit = abs(entry_price - invalidation)
    if risk_per_unit <= 0:
        return {
            "mae_r": None,
            "mfe_r": None,
            "max_adverse_price": None,
            "max_favorable_price": None,
            "invalidation_touched": None,
            "bars_used": 0,
            "entry_candle_excluded": True,
        }

    first_full_bar_ms = entry_timestamp_ms - (entry_timestamp_ms % interval_ms) + interval_ms
    eligible = [
        candle
        for candle in candles
        if candle.start_ms >= first_full_bar_ms
        and (end_timestamp_ms is None or candle.start_ms <= end_timestamp_ms)
    ]
    if not eligible:
        return {
            "mae_r": 0.0,
            "mfe_r": 0.0,
            "max_adverse_price": None,
            "max_favorable_price": None,
            "invalidation_touched": False,
            "bars_used": 0,
            "entry_candle_excluded": True,
        }

    normalized_side = side.upper()
    if normalized_side == "LONG":
        max_favorable_price = max(candle.high for candle in eligible)
        max_adverse_price = min(candle.low for candle in eligible)
        mfe = max(0.0, max_favorable_price - entry_price)
        mae = max(0.0, entry_price - max_adverse_price)
        invalidation_touched = max_adverse_price <= invalidation
    else:
        max_favorable_price = min(candle.low for candle in eligible)
        max_adverse_price = max(candle.high for candle in eligible)
        mfe = max(0.0, entry_price - max_favorable_price)
        mae = max(0.0, max_adverse_price - entry_price)
        invalidation_touched = max_adverse_price >= invalidation

    return {
        "mae_r": mae / risk_per_unit,
        "mfe_r": mfe / risk_per_unit,
        "max_adverse_price": max_adverse_price,
        "max_favorable_price": max_favorable_price,
        "invalidation_touched": invalidation_touched,
        "bars_used": len(eligible),
        "entry_candle_excluded": True,
    }


def _risk_geometry(side: str, entry_price: float, invalidation: float) -> bool:
    if side.upper() == "LONG":
        return invalidation < entry_price
    return invalidation > entry_price


def _location_evidence(evidence_present: list[str]) -> bool | None:
    if not evidence_present:
        return None
    tokens = ("LOCATION", "SUPPORT", "RESISTANCE", "RANGE", "FIB")
    normalized = [item.upper() for item in evidence_present]
    return any(any(token in item for token in tokens) for item in normalized)


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def build_entry_replay_review(
    *,
    trade_plan_id: str,
    symbol: str,
    side: str,
    setup_type: str,
    entry_timestamp_ms: int,
    entry_price: Decimal | float,
    quantity: Decimal | float | None,
    invalidation: Decimal | float,
    max_risk_percent: Decimal | float | None,
    planned: bool | None,
    confirmation_satisfied: bool | None,
    risk_policy_satisfied: bool | None,
    evidence_present: list[str],
    evidence_missing: list[str],
    confirmation_conditions: list[dict[str, Any]],
    candles_15m: list[Candle],
    candles_1h: list[Candle],
    post_end_timestamp_ms: int | None,
    thesis_flip: bool = False,
    prior_trade: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = float(entry_price)
    stop = float(invalidation)
    risk_valid = _risk_geometry(side, entry, stop)
    regime_15m = regime_at_entry(candles_15m, entry_timestamp_ms, 15 * 60_000, side)
    regime_1h = regime_at_entry(candles_1h, entry_timestamp_ms, 60 * 60_000, side)
    location_present = _location_evidence(evidence_present)

    checks: list[dict[str, str]] = []
    flags: list[str] = []
    strengths: list[str] = []
    improvements: list[str] = []
    requirements: list[str] = []

    checks.append(
        _check(
            "risk_geometry",
            "PASS" if risk_valid else "FAIL",
            "Structural invalidation is on the correct side of entry."
            if risk_valid
            else "Structural invalidation is not on the loss side of entry.",
        )
    )
    if risk_valid:
        strengths.append("Defined structural invalidation before replay evaluation.")
    else:
        flags.append("INVALID_RISK_GEOMETRY")
        improvements.append("Define a valid structural invalidation before sizing or entry.")

    if planned is True:
        checks.append(_check("planned_entry", "PASS", "Execution event was marked planned."))
        strengths.append("Entry was recorded as planned.")
    elif planned is False:
        checks.append(_check("planned_entry", "FAIL", "Execution event was marked unplanned."))
        flags.append("UNPLANNED_ENTRY")
        improvements.append("Do not enter until the setup exists in the journal plan.")
    else:
        checks.append(_check("planned_entry", "UNKNOWN", "Planned status was not recorded."))

    if confirmation_satisfied is True:
        checks.append(
            _check("confirmation", "PASS", "Recorded confirmation was satisfied at entry.")
        )
        strengths.append("Entry confirmation was recorded as satisfied.")
    elif confirmation_satisfied is False:
        checks.append(
            _check("confirmation", "FAIL", "Entry occurred before recorded confirmation.")
        )
        flags.append("ENTRY_WITHOUT_CONFIRMATION")
        improvements.append("Separate location from trigger; wait for confirmation before entry.")
        requirements.extend(
            [
                condition.get("description")
                or condition.get("condition")
                or str(condition)
                for condition in confirmation_conditions
            ]
        )
        if not confirmation_conditions:
            requirements.append("Wait for the plan's explicit confirmation trigger.")
    else:
        detail = "Confirmation status was not recorded."
        if evidence_missing:
            detail += f" Missing evidence: {', '.join(evidence_missing)}."
        checks.append(_check("confirmation", "UNKNOWN", detail))

    if risk_policy_satisfied is True:
        checks.append(_check("risk_policy", "PASS", "Risk policy was satisfied at entry."))
        strengths.append("Entry respected the recorded risk policy.")
    elif risk_policy_satisfied is False:
        checks.append(_check("risk_policy", "FAIL", "Risk policy was breached at entry."))
        flags.append("RISK_POLICY_BREACH")
        improvements.append("Resize or skip the trade before execution.")
    else:
        checks.append(_check("risk_policy", "UNKNOWN", "Risk policy status was not recorded."))

    if regime_15m["aligned"] is True:
        checks.append(_check("15m_regime", "PASS", "15m closed-bar EMA regime aligned."))
    elif regime_15m["aligned"] is False:
        checks.append(
            _check("15m_regime", "FAIL", "15m closed-bar EMA regime opposed the trade.")
        )
        flags.append("LOWER_TIMEFRAME_TREND_MISALIGNMENT")
        improvements.append("Avoid anticipatory entries against the closed-bar 15m regime.")
        requirements.append("Wait for 15m regime/structure to align or reclaim.")
    else:
        checks.append(_check("15m_regime", "UNKNOWN", "Not enough pre-entry 15m history."))

    if regime_1h["aligned"] is True:
        checks.append(_check("1h_regime", "PASS", "1h closed-bar EMA regime aligned."))
    elif regime_1h["aligned"] is False:
        checks.append(_check("1h_regime", "FAIL", "1h closed-bar EMA regime opposed the trade."))
        flags.append("HIGHER_TIMEFRAME_TREND_MISALIGNMENT")
    else:
        checks.append(_check("1h_regime", "UNKNOWN", "Not enough pre-entry 1h history."))

    if location_present is True:
        checks.append(_check("location", "PASS", "Location evidence was recorded."))
        strengths.append("Trade was taken at a recorded structural location.")
    elif location_present is False:
        checks.append(_check("location", "UNKNOWN", "Evidence exists but no location tag was found."))
    else:
        checks.append(_check("location", "UNKNOWN", "No location evidence was recorded."))

    if thesis_flip:
        checks.append(
            _check(
                "thesis_flip",
                "FAIL" if confirmation_satisfied is not True else "WARN",
                "Opposite-direction trade context was recorded shortly before this entry.",
            )
        )
        flags.append("SAME_SESSION_THESIS_FLIP")
        improvements.append(
            "After closing an opposite thesis, go flat and require an independent new setup."
        )
        requirements.append("Require fresh confirmation after an opposite-direction exit.")
    else:
        checks.append(_check("thesis_flip", "PASS", "No recent opposite-direction exit found."))

    if not risk_valid or risk_policy_satisfied is False:
        decision = "BLOCK"
        grade = "F"
    elif planned is False and confirmation_satisfied is not True:
        decision = "WAIT"
        grade = "D"
    elif thesis_flip and confirmation_satisfied is not True:
        decision = "WAIT"
        grade = "D"
    elif confirmation_satisfied is False:
        decision = "WAIT"
        grade = "C"
    elif regime_15m["aligned"] is False and confirmation_satisfied is not True:
        decision = "WAIT"
        grade = "C"
    elif (
        planned is True
        and confirmation_satisfied is True
        and risk_policy_satisfied is True
        and regime_15m["aligned"] is True
        and regime_1h["aligned"] is True
        and not thesis_flip
    ):
        decision = "ENTER"
        grade = "A"
    else:
        decision = "ENTER" if confirmation_satisfied is True else "WAIT"
        grade = "B" if decision == "ENTER" else "C"

    replay = excursion_metrics(
        candles_15m,
        side=side,
        entry_price=entry,
        invalidation=stop,
        entry_timestamp_ms=entry_timestamp_ms,
        interval_ms=15 * 60_000,
        end_timestamp_ms=post_end_timestamp_ms,
    )

    requirements = list(dict.fromkeys(item for item in requirements if item))

    risk_per_unit = abs(entry - stop) if risk_valid else None
    risk_value = (
        risk_per_unit * float(quantity)
        if risk_per_unit is not None and quantity is not None
        else None
    )

    return {
        "trade_plan_id": trade_plan_id,
        "symbol": symbol.upper(),
        "side": side.upper(),
        "setup_type": setup_type,
        "entry": {
            "timestamp_ms": entry_timestamp_ms,
            "price": entry,
            "quantity": float(quantity) if quantity is not None else None,
            "invalidation": stop,
            "risk_per_unit": risk_per_unit,
            "risk_value": risk_value,
            "max_risk_percent": float(max_risk_percent)
            if max_risk_percent is not None
            else None,
        },
        "entry_diagnostics": {
            "grade": grade,
            "decision_at_entry": decision,
            "checks": checks,
            "flags": list(dict.fromkeys(flags)),
            "what_was_good": list(dict.fromkeys(strengths)),
            "what_needed_improvement": list(dict.fromkeys(improvements)),
            "required_before_entry": requirements,
        },
        "market_at_entry": {
            "15m_regime": regime_15m,
            "1h_regime": regime_1h,
            "evidence_present": evidence_present,
            "evidence_missing": evidence_missing,
        },
        "prior_context": {
            "same_session_thesis_flip": thesis_flip,
            "prior_trade": prior_trade,
        },
        "replay": replay,
        "counterfactuals": [
            {
                "name": "confirmation_gated_entry",
                "status": "NOT_SIMULATED",
                "reason": (
                    "The MVP reports what confirmation was missing but does not invent a "
                    "counterfactual fill unless the trigger is machine-readable."
                ),
            }
        ],
        "methodology": {
            "hindsight_guard": (
                "Entry diagnostics use only bars fully closed before the entry timestamp. "
                "Post-entry bars are used only for MAE/MFE and invalidation-touch replay."
            ),
            "entry_candle_policy": (
                "The candle containing the fill is excluded from excursion metrics because "
                "OHLC data cannot prove whether its high or low occurred before the fill."
            ),
        },
    }
