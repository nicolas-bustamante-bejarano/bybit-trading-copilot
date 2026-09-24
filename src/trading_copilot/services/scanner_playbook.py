from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from trading_copilot.domain.models import Regime, SetupState, Side
from trading_copilot.domain.playbook import (
    FibAnchors,
    PlaybookEvaluationRequest,
    PlaybookType,
    RangeBounds,
)
from trading_copilot.domain.scanner import (
    ScannerDataStatus,
    ScannerResult,
    ScannerSetupType,
    ScannerStatus,
)
from trading_copilot.persistence.models import (
    FibDefinitionRow,
    RangeDefinitionRow,
    TradePlanRow,
    WatchedSetupRow,
)
from trading_copilot.services.playbook import evaluate_playbook
from trading_copilot.services.scanner_structure import resolve_plan_structure

STRUCTURAL_BLOCKERS = {
    "STRUCTURE_REQUIRED",
    "AMBIGUOUS_STRUCTURE",
    "INCOMPATIBLE_LINKED_PLAN",
}

STATE_MAPPING = {
    SetupState.NO_SETUP: ScannerStatus.IGNORE,
    SetupState.CONTEXT_VALID: ScannerStatus.WATCH,
    SetupState.APPROACHING_LOCATION: ScannerStatus.APPROACHING_LOCATION,
    SetupState.AT_LOCATION: ScannerStatus.AT_LOCATION,
    SetupState.CONFIRMATION_DEVELOPING: ScannerStatus.REACTION_DEVELOPING,
    SetupState.WAITING_FOR_TRIGGER: ScannerStatus.TRIGGER_ARMED,
    SetupState.INVALIDATED: ScannerStatus.IGNORE,
}

NEXT_CONDITIONS = {
    ScannerStatus.WATCH: "approach actionable structure",
    ScannerStatus.APPROACHING_LOCATION: "reach actionable location",
    ScannerStatus.AT_LOCATION: "await supportive reaction / pre-trigger evidence",
    ScannerStatus.REACTION_DEVELOPING: "await complete pre-trigger confirmation",
    ScannerStatus.TRIGGER_ARMED: "await lower-timeframe trigger engine",
}


def _setup_details(setup_type: ScannerSetupType) -> tuple[Side, str, PlaybookType]:
    side = Side.LONG if setup_type.value.endswith("_LONG") else Side.SHORT
    if setup_type in {
        ScannerSetupType.TREND_PULLBACK_LONG,
        ScannerSetupType.TREND_PULLBACK_SHORT,
    }:
        return side, "TREND_PULLBACK", PlaybookType.TREND_PULLBACK
    if setup_type == ScannerSetupType.RANGE_LONG:
        return side, "RANGE", PlaybookType.RANGE_LONG
    if setup_type == ScannerSetupType.RANGE_SHORT:
        return side, "RANGE", PlaybookType.RANGE_SHORT
    raise ValueError(f"unsupported scanner playbook: {setup_type}")


def _context(
    *, playbook: PlaybookType, side: Side, regime_1h: Regime, regime_4h: Regime
) -> tuple[dict, bool]:
    required_4h = (
        Regime.RANGE
        if playbook in {PlaybookType.RANGE_LONG, PlaybookType.RANGE_SHORT}
        else (Regime.UPTREND if side == Side.LONG else Regime.DOWNTREND)
    )
    valid = regime_4h == required_4h
    return (
        {
            "regime_1h": regime_1h,
            "regime_4h": regime_4h,
            "required_regime_4h": required_4h,
            "valid": valid,
        },
        valid,
    )


def _map_state(state: SetupState) -> tuple[ScannerStatus, str | None]:
    if state in {SetupState.TRIGGERED, SetupState.READY}:
        return ScannerStatus.TRIGGER_ARMED, f"UNEXPECTED_PLAYBOOK_STATE:{state.value.upper()}"
    return STATE_MAPPING[state], None


def evaluate_scanner_playbook(
    *,
    watched: WatchedSetupRow,
    plans: Iterable[TradePlanRow],
    definitions: Iterable[FibDefinitionRow | RangeDefinitionRow],
    price: float,
    regime_1h: Regime,
    regime_4h: Regime,
    stoch_k: float | None = None,
    stoch_d: float | None = None,
    prev_stoch_k: float | None = None,
    prev_stoch_d: float | None = None,
    reaction_state: str | None = None,
    invalidation_breached: bool = False,
    approach_tolerance_bps: float = 50.0,
    data_status: ScannerDataStatus = ScannerDataStatus.CONFIRMED,
    evaluated_at: datetime | None = None,
) -> ScannerResult:
    setup_type = ScannerSetupType(watched.setup_type)
    side, family, playbook = _setup_details(setup_type)
    context, context_valid = _context(
        playbook=playbook,
        side=side,
        regime_1h=regime_1h,
        regime_4h=regime_4h,
    )
    resolution = resolve_plan_structure(
        watched=watched,
        plans=plans,
        definitions=definitions,
        side=side.value,
        family=family,
    )
    linked_trade_plan_id = resolution.trade_plan_id or watched.trade_plan_id

    if resolution.reason in STRUCTURAL_BLOCKERS:
        status = ScannerStatus.IGNORE if not context_valid else ScannerStatus.WATCH
        return ScannerResult(
            symbol=watched.symbol,
            setup_type=setup_type,
            side=side.value.upper(),
            status=status,
            price=price,
            evaluated_at=evaluated_at or datetime.now(UTC),
            context=context,
            blocking_reasons=[resolution.reason],
            next_conditions=[NEXT_CONDITIONS[status]] if status in NEXT_CONDITIONS else [],
            data_status=data_status,
            linked_trade_plan_id=linked_trade_plan_id,
        )

    request = PlaybookEvaluationRequest(
        symbol=watched.symbol,
        playbook=playbook,
        side=side,
        price=price,
        regime_1h=regime_1h,
        regime_4h=regime_4h,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        prev_stoch_k=prev_stoch_k,
        prev_stoch_d=prev_stoch_d,
        fib=(
            FibAnchors(
                swing_low=float(resolution.fib.swing_low),
                swing_high=float(resolution.fib.swing_high),
            )
            if resolution.fib is not None
            else None
        ),
        range_bounds=(
            RangeBounds(
                low=float(resolution.range.range_low),
                high=float(resolution.range.range_high),
            )
            if resolution.range is not None
            else None
        ),
        reaction_state=reaction_state,
        trigger_confirmed=False,
        invalidation_breached=invalidation_breached,
        approach_tolerance_bps=approach_tolerance_bps,
    )
    evaluation = evaluate_playbook(request)
    status, diagnostic = _map_state(evaluation["state"])
    blockers = list(evaluation["missing_required_conditions"])
    if diagnostic is not None:
        blockers.append(diagnostic)

    return ScannerResult(
        symbol=watched.symbol,
        setup_type=setup_type,
        side=side.value.upper(),
        status=status,
        price=price,
        evaluated_at=evaluated_at or datetime.now(UTC),
        context=context,
        location=evaluation["location"],
        structure=_visual_structure(
            resolution=resolution,
            family=family,
            side=side,
            location=evaluation["location"],
            approach_tolerance_bps=approach_tolerance_bps,
        ),
        reaction={
            "state": evaluation["reaction_state"],
            "momentum": evaluation["momentum"],
        },
        conditions=evaluation["conditions"],
        blocking_reasons=blockers,
        next_conditions=[NEXT_CONDITIONS[status]] if status in NEXT_CONDITIONS else [],
        data_status=data_status,
        linked_trade_plan_id=linked_trade_plan_id,
    )


def _visual_structure(
    *,
    resolution,
    family: str,
    side: Side,
    location: dict,
    approach_tolerance_bps: float,
) -> dict:
    if family == "TREND_PULLBACK" and resolution.fib is not None:
        enabled_zones = [zone for zone in location.get("zones", []) if zone.get("enabled")]
        active_name = location.get("active_zone")
        actionable = next(
            (zone for zone in enabled_zones if zone.get("name") == active_name),
            min(enabled_zones, key=lambda zone: zone["distance_bps"], default=None),
        )
        approach_zone = None
        if actionable is not None:
            lower = float(actionable["lower"])
            upper = float(actionable["upper"])
            approach_zone = {
                "lower": lower * (1 - approach_tolerance_bps / 10_000),
                "upper": upper * (1 + approach_tolerance_bps / 10_000),
            }
        return {
            "definition_id": resolution.fib.id,
            "trade_plan_id": resolution.trade_plan_id,
            "type": "FIB_RETRACEMENT",
            "symbol": resolution.fib.symbol,
            "direction": resolution.fib.direction,
            "swing_low": float(resolution.fib.swing_low),
            "swing_high": float(resolution.fib.swing_high),
            "levels": location.get("levels", {}),
            "actionable_zone": actionable,
            "approach_zone": approach_zone,
        }
    if family == "RANGE" and resolution.range is not None:
        low = float(resolution.range.range_low)
        high = float(resolution.range.range_high)
        actionable_level = low if side == Side.LONG else high
        tolerance = actionable_level * approach_tolerance_bps / 10_000
        return {
            "definition_id": resolution.range.id,
            "trade_plan_id": resolution.trade_plan_id,
            "type": "RANGE",
            "symbol": resolution.range.symbol,
            "range_low": low,
            "range_high": high,
            "actionable_level": actionable_level,
            "approach_zone": {
                "lower": actionable_level - tolerance,
                "upper": actionable_level + tolerance,
            },
        }
    return {}
