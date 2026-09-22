from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from trading_copilot.domain.models import Regime, SetupState, Side
from trading_copilot.domain.playbook import PlaybookEvaluationRequest, PlaybookType
from trading_copilot.services.indicators import fib_retracements


@dataclass(slots=True)
class Condition:
    name: str
    status: bool | None
    evidence: Any
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_playbook(request: PlaybookEvaluationRequest) -> dict[str, Any]:
    if request.invalidation_breached:
        return _result(
            request,
            SetupState.INVALIDATED,
            [Condition("invalidation_intact", False, "manual invalidation breached", True)],
            location={},
            momentum=_momentum_context(request),
        )

    if request.playbook == PlaybookType.TREND_PULLBACK:
        return _evaluate_trend_pullback(request)
    return _evaluate_range(request)


def _evaluate_trend_pullback(request: PlaybookEvaluationRequest) -> dict[str, Any]:
    expected_regime = Regime.UPTREND if request.side == Side.LONG else Regime.DOWNTREND
    context_valid = request.regime_4h == expected_regime
    strong_alignment = context_valid and request.regime_1h == expected_regime

    fib = request.fib
    if fib is None:
        raise ValueError("trend pullback requires fib anchors")
    levels = fib_retracements(fib.swing_low, fib.swing_high, request.side.value)
    location = _fib_location(
        price=request.price,
        levels=levels,
        allow_shallow=strong_alignment,
        approach_tolerance_bps=request.approach_tolerance_bps,
    )
    momentum = _momentum_context(request)
    momentum_supportive = _momentum_supportive(request.side, momentum)
    reaction_supportive = _reaction_supportive(request.side, request.reaction_state)

    conditions = [
        Condition(
            "4h_regime_aligned",
            context_valid,
            {"actual": request.regime_4h, "required": expected_regime},
            True,
        ),
        Condition(
            "1h_regime_aligned",
            request.regime_1h == expected_regime,
            {"actual": request.regime_1h, "preferred": expected_regime},
        ),
        Condition(
            "at_actionable_fib_location",
            location["at_location"],
            location,
            True,
        ),
        Condition("momentum_reset", momentum_supportive, momentum),
        Condition(
            "reaction_supportive",
            reaction_supportive if request.reaction_state is not None else None,
            request.reaction_state,
        ),
        Condition("trigger_confirmed", request.trigger_confirmed, request.trigger_confirmed, True),
    ]

    state = _resolve_state(
        context_valid=context_valid,
        approaching=location["approaching"],
        at_location=location["at_location"],
        momentum_supportive=momentum_supportive,
        reaction_supportive=reaction_supportive,
        reaction_available=request.reaction_state is not None,
        trigger_confirmed=request.trigger_confirmed,
    )
    return _result(request, state, conditions, location=location, momentum=momentum)


def _evaluate_range(request: PlaybookEvaluationRequest) -> dict[str, Any]:
    bounds = request.range_bounds
    if bounds is None:
        raise ValueError("range playbook requires range bounds")

    context_valid = request.regime_4h == Regime.RANGE
    span = bounds.high - bounds.low
    range_position = (request.price - bounds.low) / span

    if request.side == Side.LONG:
        at_location = range_position <= 0.20
        approaching = range_position <= 0.30
        location_label = "lower_range_extreme"
    else:
        at_location = range_position >= 0.80
        approaching = range_position >= 0.70
        location_label = "upper_range_extreme"

    location = {
        "type": location_label,
        "range_low": bounds.low,
        "range_high": bounds.high,
        "range_mid": (bounds.low + bounds.high) / 2,
        "range_position": range_position,
        "at_location": at_location,
        "approaching": approaching,
    }
    momentum = _momentum_context(request)
    momentum_supportive = _momentum_supportive(request.side, momentum)
    reaction_supportive = _reaction_supportive(request.side, request.reaction_state)

    conditions = [
        Condition(
            "4h_range_regime",
            context_valid,
            {"actual": request.regime_4h, "required": Regime.RANGE},
            True,
        ),
        Condition("at_range_extreme", at_location, location, True),
        Condition("momentum_reset", momentum_supportive, momentum),
        Condition(
            "reaction_supportive",
            reaction_supportive if request.reaction_state is not None else None,
            request.reaction_state,
        ),
        Condition("trigger_confirmed", request.trigger_confirmed, request.trigger_confirmed, True),
    ]

    state = _resolve_state(
        context_valid=context_valid,
        approaching=approaching,
        at_location=at_location,
        momentum_supportive=momentum_supportive,
        reaction_supportive=reaction_supportive,
        reaction_available=request.reaction_state is not None,
        trigger_confirmed=request.trigger_confirmed,
    )
    return _result(request, state, conditions, location=location, momentum=momentum)


def _fib_location(
    *,
    price: float,
    levels: dict[str, float],
    allow_shallow: bool,
    approach_tolerance_bps: float,
) -> dict[str, Any]:
    zone_specs = [
        ("shallow", "0.382", "0.500", allow_shallow),
        ("mid", "0.500", "0.618", True),
        ("primary", "0.618", "0.786", True),
        ("deep", "0.786", "0.886", True),
    ]
    zones: list[dict[str, Any]] = []
    active_zone: str | None = None
    nearest_distance_bps: float | None = None

    for name, left_ratio, right_ratio, enabled in zone_specs:
        lower = min(levels[left_ratio], levels[right_ratio])
        upper = max(levels[left_ratio], levels[right_ratio])
        inside = lower <= price <= upper
        distance = _distance_to_interval_bps(price, lower, upper)
        zones.append(
            {
                "name": name,
                "lower": lower,
                "upper": upper,
                "enabled": enabled,
                "inside": inside,
                "distance_bps": distance,
            }
        )
        if enabled and inside:
            active_zone = name
        if enabled and (nearest_distance_bps is None or distance < nearest_distance_bps):
            nearest_distance_bps = distance

    at_location = active_zone is not None
    approaching = at_location or (
        nearest_distance_bps is not None and nearest_distance_bps <= approach_tolerance_bps
    )
    return {
        "type": "fib_retracement",
        "active_zone": active_zone,
        "at_location": at_location,
        "approaching": approaching,
        "nearest_actionable_distance_bps": nearest_distance_bps,
        "levels": levels,
        "zones": zones,
    }


def _distance_to_interval_bps(price: float, lower: float, upper: float) -> float:
    if lower <= price <= upper:
        return 0.0
    nearest = lower if price < lower else upper
    return abs(price - nearest) / price * 10_000


def _momentum_context(request: PlaybookEvaluationRequest) -> dict[str, Any]:
    k = request.stoch_k
    d = request.stoch_d
    prev_k = request.prev_stoch_k
    prev_d = request.prev_stoch_d

    bullish_cross = (
        None not in (k, d, prev_k, prev_d)
        and prev_k <= prev_d
        and k > d
    )
    bearish_cross = (
        None not in (k, d, prev_k, prev_d)
        and prev_k >= prev_d
        and k < d
    )
    exiting_oversold = prev_k is not None and k is not None and prev_k <= 20 < k
    exiting_overbought = prev_k is not None and k is not None and prev_k >= 80 > k

    return {
        "k": k,
        "d": d,
        "prev_k": prev_k,
        "prev_d": prev_d,
        "oversold": k is not None and k <= 20,
        "overbought": k is not None and k >= 80,
        "bullish_cross": bullish_cross,
        "bearish_cross": bearish_cross,
        "exiting_oversold": exiting_oversold,
        "exiting_overbought": exiting_overbought,
    }


def _momentum_supportive(side: Side, momentum: dict[str, Any]) -> bool:
    if side == Side.LONG:
        return bool(
            momentum["oversold"]
            or momentum["exiting_oversold"]
            or (momentum["bullish_cross"] and (momentum["k"] or 100) <= 50)
        )
    return bool(
        momentum["overbought"]
        or momentum["exiting_overbought"]
        or (momentum["bearish_cross"] and (momentum["k"] or 0) >= 50)
    )


def _reaction_supportive(side: Side, reaction_state: str | None) -> bool:
    if reaction_state is None:
        return False
    if side == Side.LONG:
        return reaction_state in {"sell_absorption", "buy_continuation"}
    return reaction_state in {"buy_absorption", "sell_continuation"}


def _resolve_state(
    *,
    context_valid: bool,
    approaching: bool,
    at_location: bool,
    momentum_supportive: bool,
    reaction_supportive: bool,
    reaction_available: bool,
    trigger_confirmed: bool,
) -> SetupState:
    if not context_valid:
        return SetupState.NO_SETUP
    if not at_location:
        return SetupState.APPROACHING_LOCATION if approaching else SetupState.CONTEXT_VALID

    confirmation_count = int(momentum_supportive) + int(reaction_supportive)
    if trigger_confirmed:
        if confirmation_count >= 1:
            return SetupState.READY
        return SetupState.TRIGGERED
    if confirmation_count >= 2:
        return SetupState.WAITING_FOR_TRIGGER
    if confirmation_count == 1 or (reaction_available and reaction_supportive):
        return SetupState.CONFIRMATION_DEVELOPING
    return SetupState.AT_LOCATION


def _result(
    request: PlaybookEvaluationRequest,
    state: SetupState,
    conditions: list[Condition],
    *,
    location: dict[str, Any],
    momentum: dict[str, Any],
) -> dict[str, Any]:
    missing = [
        condition.name
        for condition in conditions
        if condition.required and condition.status is False
    ]
    return {
        "symbol": request.symbol.upper(),
        "playbook": request.playbook,
        "side": request.side,
        "state": state,
        "price": request.price,
        "location": location,
        "momentum": momentum,
        "reaction_state": request.reaction_state,
        "trigger_confirmed": request.trigger_confirmed,
        "conditions": [condition.as_dict() for condition in conditions],
        "missing_required_conditions": missing,
        "ready_for_manual_execution": state == SetupState.READY,
    }
