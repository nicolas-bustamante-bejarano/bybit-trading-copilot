from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import ScannerSetupType
from trading_copilot.domain.trigger import (
    LowerTimeframeBar,
    TriggerEvaluationRequest,
    TriggerPattern,
    TriggerResult,
    TriggerState,
)
from trading_copilot.services.reaction import ReactionState

DEVIATION_RECLAIM_SETUPS = {
    ScannerSetupType.TREND_PULLBACK_LONG,
    ScannerSetupType.TREND_PULLBACK_SHORT,
    ScannerSetupType.RANGE_LONG,
    ScannerSetupType.RANGE_SHORT,
}

SUPPORTIVE_REACTIONS = {
    Side.LONG: {ReactionState.SELL_ABSORPTION, ReactionState.BUY_CONTINUATION},
    Side.SHORT: {ReactionState.BUY_ABSORPTION, ReactionState.SELL_CONTINUATION},
}


@dataclass(frozen=True)
class _SequenceEvidence:
    state: TriggerState
    anchor: LowerTimeframeBar | None = None
    confirmation: LowerTimeframeBar | None = None


def _ordered_unique_bars(
    bars: Iterable[LowerTimeframeBar],
) -> list[LowerTimeframeBar]:
    """Sort bars and collapse exact duplicates deterministically.

    Two different bars claiming the same interval are invalid rather than silently
    choosing one observation over another.
    """
    by_start: dict[int, LowerTimeframeBar] = {}
    for bar in bars:
        existing = by_start.get(bar.start_ms)
        if existing is not None and existing != bar:
            raise ValueError(f"conflicting duplicate bar timestamp: {bar.start_ms}")
        by_start[bar.start_ms] = bar
    ordered = sorted(by_start.values(), key=lambda bar: (bar.end_ms, bar.start_ms))
    if len({bar.end_ms for bar in ordered}) != len(ordered):
        raise ValueError("conflicting duplicate bar end timestamp")
    return ordered


def _completed_5m_bars(
    bars: Iterable[LowerTimeframeBar], armed_at_ms: float, evaluated_at_ms: float
) -> list[LowerTimeframeBar]:
    """Use only fully post-arm completed 5m candles for trigger structure."""
    eligible = (
        bar
        for bar in bars
        if bar.start_ms >= armed_at_ms and bar.end_ms <= evaluated_at_ms
    )
    return _ordered_unique_bars(eligible)


def _completed_15m_bars(
    bars: Iterable[LowerTimeframeBar], armed_at_ms: float, evaluated_at_ms: float
) -> list[LowerTimeframeBar]:
    """Use post-arm completed closes; the 15m wick is not trigger structure."""
    eligible = (
        bar
        for bar in bars
        if bar.end_ms > armed_at_ms and bar.end_ms <= evaluated_at_ms
    )
    return _ordered_unique_bars(eligible)


def _pattern(setup_type: ScannerSetupType) -> TriggerPattern:
    return (
        TriggerPattern.DEVIATION_RECLAIM
        if setup_type in DEVIATION_RECLAIM_SETUPS
        else TriggerPattern.RETEST_HOLD
    )


def _reaction_support(side: Side, reaction_state: str | None) -> bool | None:
    if reaction_state is None:
        return None
    try:
        reaction = ReactionState(reaction_state)
    except ValueError as exc:
        raise ValueError(f"unsupported reaction_state: {reaction_state}") from exc
    return reaction in SUPPORTIVE_REACTIONS[side]


def _local_acceptance(
    bars: list[LowerTimeframeBar], side: Side, reference_level: float
) -> bool | None:
    if not bars:
        return None
    close = bars[-1].close
    return close > reference_level if side == Side.LONG else close < reference_level


def _failure_level(reference: float, tolerance_bps: float, side: Side) -> float:
    adjustment = tolerance_bps / 10_000
    return reference * (1 - adjustment if side == Side.LONG else 1 + adjustment)


def _failed(bar: LowerTimeframeBar, failure_level: float, side: Side) -> bool:
    return bar.close < failure_level if side == Side.LONG else bar.close > failure_level


def _is_reclaim(bar: LowerTimeframeBar, reference: float, side: Side) -> bool:
    if side == Side.LONG:
        return bar.low < reference and bar.close > reference
    return bar.high > reference and bar.close < reference


def _continued(bar: LowerTimeframeBar, anchor: LowerTimeframeBar, side: Side) -> bool:
    return bar.close > anchor.high if side == Side.LONG else bar.close < anchor.low


def _evaluate_deviation_reclaim(
    bars: list[LowerTimeframeBar],
    *,
    reference: float,
    failure_tolerance_bps: float,
    side: Side,
) -> _SequenceEvidence:
    anchor: LowerTimeframeBar | None = None
    developing = False
    failed_anchor: LowerTimeframeBar | None = None
    failure_level = _failure_level(reference, failure_tolerance_bps, side)
    for bar in bars:
        if _is_reclaim(bar, reference, side):
            anchor = bar
            developing = False
            failed_anchor = None
            continue
        if anchor is None:
            continue
        developing = True
        if _failed(bar, failure_level, side):
            failed_anchor = anchor
            anchor = None
            developing = False
            continue
        if _continued(bar, anchor, side):
            return _SequenceEvidence(TriggerState.DEVELOPING, anchor, bar)
    if anchor is not None:
        return _SequenceEvidence(
            TriggerState.DEVELOPING if developing else TriggerState.RECLAIMED,
            anchor,
        )
    if failed_anchor is not None:
        return _SequenceEvidence(TriggerState.FAILED, failed_anchor)
    return _SequenceEvidence(TriggerState.WAITING)


def _touches_band(
    bar: LowerTimeframeBar, lower: float, upper: float
) -> bool:
    return bar.high >= lower and bar.low <= upper


def _evaluate_retest_hold(
    bars: list[LowerTimeframeBar],
    *,
    reference: float,
    retest_tolerance_bps: float,
    failure_tolerance_bps: float,
    side: Side,
) -> _SequenceEvidence:
    adjustment = retest_tolerance_bps / 10_000
    lower, upper = reference * (1 - adjustment), reference * (1 + adjustment)
    failure_level = _failure_level(reference, failure_tolerance_bps, side)
    anchor: LowerTimeframeBar | None = None
    touched = False
    failed_anchor: LowerTimeframeBar | None = None
    for bar in bars:
        touches = _touches_band(bar, lower, upper)
        holds = bar.close > reference if side == Side.LONG else bar.close < reference
        if anchor is not None:
            if _failed(bar, failure_level, side):
                failed_anchor = anchor
                anchor = None
                touched = False
                continue
            if _continued(bar, anchor, side):
                return _SequenceEvidence(TriggerState.DEVELOPING, anchor, bar)
        if anchor is None and touches:
            touched = True
            failed_anchor = None
        if anchor is None and touched:
            if _failed(bar, failure_level, side):
                failed_anchor = bar
                touched = False
                continue
            if holds:
                anchor = bar
                failed_anchor = None
    if anchor is not None:
        return _SequenceEvidence(TriggerState.RETEST_HELD, anchor)
    if failed_anchor is not None:
        return _SequenceEvidence(TriggerState.FAILED, failed_anchor)
    return _SequenceEvidence(TriggerState.DEVELOPING if touched else TriggerState.WAITING)


def evaluate_lower_timeframe_trigger(request: TriggerEvaluationRequest) -> TriggerResult:
    armed_at_ms = request.armed_at.timestamp() * 1000
    evaluated_at_ms = request.evaluated_at.timestamp() * 1000
    bars_5m = _completed_5m_bars(request.bars_5m, armed_at_ms, evaluated_at_ms)
    bars_15m = _completed_15m_bars(request.bars_15m, armed_at_ms, evaluated_at_ms)
    pattern = _pattern(request.setup_type)
    reaction_supportive = _reaction_support(request.side, request.reaction_state)
    local_acceptance = _local_acceptance(
        bars_15m, request.side, request.reference_level
    )

    if not bars_5m:
        return TriggerResult(
            symbol=request.symbol,
            setup_type=request.setup_type,
            side=request.side,
            pattern=pattern,
            state=TriggerState.INDETERMINATE,
            reference_level=request.reference_level,
            armed_at=request.armed_at,
            evaluated_at=request.evaluated_at,
            trigger_confirmed=False,
            local_15m_acceptance=local_acceptance,
            reaction_state=request.reaction_state,
            reaction_supportive=reaction_supportive,
            evidence_missing=["COMPLETED_5M_BARS"],
            blocking_reasons=["LOWER_TIMEFRAME_DATA_REQUIRED"],
            next_conditions=["await completed 5m trigger bars"],
        )

    if pattern == TriggerPattern.DEVIATION_RECLAIM:
        sequence = _evaluate_deviation_reclaim(
            bars_5m,
            reference=request.reference_level,
            failure_tolerance_bps=request.failure_tolerance_bps,
            side=request.side,
        )
        anchor_evidence = "DEVIATION_RECLAIM"
        anchor_next = "await deviation and reclaim"
    else:
        sequence = _evaluate_retest_hold(
            bars_5m,
            reference=request.reference_level,
            retest_tolerance_bps=request.retest_tolerance_bps,
            failure_tolerance_bps=request.failure_tolerance_bps,
            side=request.side,
        )
        anchor_evidence = "RETEST_HOLD"
        anchor_next = "await retest hold"

    state = sequence.state
    confirmed = sequence.confirmation is not None and local_acceptance is True
    if confirmed:
        state = TriggerState.CONFIRMED
    elif sequence.confirmation is not None:
        state = TriggerState.DEVELOPING

    evidence_present: list[str] = []
    evidence_missing: list[str] = []
    blocking_reasons: list[str] = []
    next_conditions: list[str] = []
    if sequence.anchor is not None:
        evidence_present.append(anchor_evidence)
    else:
        evidence_missing.append(anchor_evidence)
    if sequence.confirmation is not None:
        evidence_present.append("STRUCTURE_ROTATION")
    else:
        evidence_missing.append("STRUCTURE_ROTATION")
    if local_acceptance is True:
        evidence_present.append("LOCAL_15M_ACCEPTANCE")
    else:
        evidence_missing.append("LOCAL_15M_ACCEPTANCE")
    if state == TriggerState.FAILED:
        blocking_reasons.append("TRIGGER_ATTEMPT_FAILED")
        next_conditions.append(anchor_next)
    elif sequence.anchor is None:
        next_conditions.append(anchor_next)
    elif sequence.confirmation is None:
        next_conditions.append("await 5m structure rotation")
    elif local_acceptance is not True:
        blocking_reasons.append("LOCAL_15M_ACCEPTANCE_REQUIRED")
        next_conditions.append("await supportive completed 15m close")

    return TriggerResult(
        symbol=request.symbol,
        setup_type=request.setup_type,
        side=request.side,
        pattern=pattern,
        state=state,
        reference_level=request.reference_level,
        armed_at=request.armed_at,
        evaluated_at=request.evaluated_at,
        trigger_confirmed=confirmed,
        anchor_bar_end_ms=sequence.anchor.end_ms if sequence.anchor else None,
        confirmation_bar_end_ms=(
            sequence.confirmation.end_ms if sequence.confirmation else None
        ),
        local_15m_acceptance=local_acceptance,
        reaction_state=request.reaction_state,
        reaction_supportive=reaction_supportive,
        anchor_high=sequence.anchor.high if sequence.anchor else None,
        anchor_low=sequence.anchor.low if sequence.anchor else None,
        anchor_close=sequence.anchor.close if sequence.anchor else None,
        confirmation_close=(
            sequence.confirmation.close if sequence.confirmation else None
        ),
        evidence_present=evidence_present,
        evidence_missing=evidence_missing,
        blocking_reasons=blocking_reasons,
        next_conditions=next_conditions,
    )
