from datetime import UTC, datetime
from inspect import getsource

import pytest
from pydantic import ValidationError

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import ScannerSetupType
from trading_copilot.domain.trigger import (
    LowerTimeframeBar,
    TriggerEvaluationRequest,
    TriggerPattern,
    TriggerResult,
    TriggerState,
)
from trading_copilot.services import trigger_engine
from trading_copilot.services.trigger_engine import evaluate_lower_timeframe_trigger


def bar(
    end: int,
    *,
    open: float = 100,
    high: float = 101,
    low: float = 99,
    close: float = 100,
) -> LowerTimeframeBar:
    return LowerTimeframeBar(
        start_ms=end - 100,
        end_ms=end,
        open=open,
        high=high,
        low=low,
        close=close,
    )


def request(
    *,
    setup_type: ScannerSetupType = ScannerSetupType.TREND_PULLBACK_LONG,
    side: Side = Side.LONG,
    bars_5m: list[LowerTimeframeBar] | None = None,
    bars_15m: list[LowerTimeframeBar] | None = None,
    armed_ms: int = 0,
    evaluated_ms: int = 10_000,
    reaction_state: str | None = None,
    reference_level: float = 100,
    retest_tolerance_bps: float = 25,
    failure_tolerance_bps: float = 25,
) -> TriggerEvaluationRequest:
    return TriggerEvaluationRequest(
        symbol="btcusdt",
        setup_type=setup_type,
        side=side,
        reference_level=reference_level,
        bars_5m=bars_5m or [],
        bars_15m=bars_15m or [],
        armed_at=datetime.fromtimestamp(armed_ms / 1000, UTC),
        evaluated_at=datetime.fromtimestamp(evaluated_ms / 1000, UTC),
        retest_tolerance_bps=retest_tolerance_bps,
        failure_tolerance_bps=failure_tolerance_bps,
        reaction_state=reaction_state,
    )


def long_reclaim(end: int = 1_000) -> LowerTimeframeBar:
    return bar(end, open=100.2, high=101, low=99, close=100.5)


def long_rotation(end: int = 2_000) -> LowerTimeframeBar:
    return bar(end, open=100.5, high=102, low=100.2, close=101.5)


def short_reclaim(end: int = 1_000) -> LowerTimeframeBar:
    return bar(end, open=99.8, high=101, low=99, close=99.5)


def short_rotation(end: int = 2_000) -> LowerTimeframeBar:
    return bar(end, open=99.5, high=99.8, low=98, close=98.5)


def supportive_15m(side: Side, end: int = 3_000) -> LowerTimeframeBar:
    close = 100.5 if side == Side.LONG else 99.5
    return bar(end, high=101, low=99, close=close)


@pytest.mark.parametrize(
    ("setup_type", "side"),
    [
        (ScannerSetupType.TREND_PULLBACK_LONG, Side.LONG),
        (ScannerSetupType.TREND_PULLBACK_SHORT, Side.SHORT),
        (ScannerSetupType.RANGE_LONG, Side.LONG),
        (ScannerSetupType.RANGE_SHORT, Side.SHORT),
    ],
)
def test_generic_setups_map_to_deviation_reclaim(setup_type, side):
    result = evaluate_lower_timeframe_trigger(
        request(setup_type=setup_type, side=side, bars_5m=[bar(1_000)])
    )

    assert result.pattern == TriggerPattern.DEVIATION_RECLAIM


@pytest.mark.parametrize(
    ("setup_type", "side"),
    [
        (ScannerSetupType.MACRO_BREAKOUT_LONG, Side.LONG),
        (ScannerSetupType.MACRO_BREAKOUT_SHORT, Side.SHORT),
    ],
)
def test_macro_setups_map_to_retest_hold(setup_type, side):
    result = evaluate_lower_timeframe_trigger(
        request(setup_type=setup_type, side=side, bars_5m=[bar(1_000, low=102, high=104, open=103, close=103)])
    )

    assert result.pattern == TriggerPattern.RETEST_HOLD


def test_long_without_deviation_waits():
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[bar(1_000, low=100, close=100.5)])
    )

    assert result.state == TriggerState.WAITING
    assert not result.trigger_confirmed


def test_long_reclaim_is_anchor_only():
    result = evaluate_lower_timeframe_trigger(request(bars_5m=[long_reclaim()]))

    assert result.state == TriggerState.RECLAIMED
    assert result.anchor_bar_end_ms == 1_000
    assert result.confirmation_bar_end_ms is None
    assert result.anchor_high == 101


def test_long_later_rotation_and_15m_acceptance_confirm():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(), long_rotation()],
            bars_15m=[supportive_15m(Side.LONG)],
        )
    )

    assert result.state == TriggerState.CONFIRMED
    assert result.trigger_confirmed
    assert result.confirmation_bar_end_ms == 2_000
    assert result.confirmation_close == 101.5
    assert result.local_15m_acceptance is True
    assert "STRUCTURE_ROTATION" in result.evidence_present


def test_same_long_reclaim_candle_cannot_confirm():
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[long_reclaim()], bars_15m=[supportive_15m(Side.LONG)])
    )

    assert result.state == TriggerState.RECLAIMED
    assert not result.trigger_confirmed


def test_long_adverse_close_before_rotation_fails():
    failure = bar(2_000, open=100, high=100.2, low=99.4, close=99.7)
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[long_reclaim(), failure])
    )

    assert result.state == TriggerState.FAILED
    assert result.blocking_reasons == ["TRIGGER_ATTEMPT_FAILED"]


def test_newer_long_reclaim_replaces_failed_attempt():
    failure = bar(2_000, open=100, high=100.2, low=99.4, close=99.7)
    newer = long_reclaim(3_000)
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[long_reclaim(), failure, newer])
    )

    assert result.state == TriggerState.RECLAIMED
    assert result.anchor_bar_end_ms == 3_000


def test_partial_long_continuation_is_ignored():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(), long_rotation(3_000)],
            bars_15m=[supportive_15m(Side.LONG)],
            evaluated_ms=2_000,
        )
    )

    assert result.state == TriggerState.RECLAIMED
    assert result.confirmation_bar_end_ms is None


def test_exact_end_time_boundary_is_included():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(), long_rotation(2_000)],
            bars_15m=[supportive_15m(Side.LONG, 2_000)],
            evaluated_ms=2_000,
        )
    )

    assert result.state == TriggerState.CONFIRMED


def test_short_reclaim_and_rotation_confirm():
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.TREND_PULLBACK_SHORT,
            side=Side.SHORT,
            bars_5m=[short_reclaim(), short_rotation()],
            bars_15m=[supportive_15m(Side.SHORT)],
        )
    )

    assert result.state == TriggerState.CONFIRMED
    assert result.anchor_low == 99
    assert result.confirmation_close == 98.5


def test_short_without_deviation_waits():
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.TREND_PULLBACK_SHORT,
            side=Side.SHORT,
            bars_5m=[bar(1_000, high=100, close=99.5)],
        )
    )

    assert result.state == TriggerState.WAITING


def test_short_reclaim_is_anchor_only():
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.RANGE_SHORT,
            side=Side.SHORT,
            bars_5m=[short_reclaim()],
            bars_15m=[supportive_15m(Side.SHORT)],
        )
    )

    assert result.state == TriggerState.RECLAIMED
    assert not result.trigger_confirmed


def test_partial_short_continuation_is_ignored():
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.RANGE_SHORT,
            side=Side.SHORT,
            bars_5m=[short_reclaim(), short_rotation(3_000)],
            bars_15m=[supportive_15m(Side.SHORT)],
            evaluated_ms=2_000,
        )
    )

    assert result.state == TriggerState.RECLAIMED


def test_short_exact_end_time_boundary_is_included():
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.RANGE_SHORT,
            side=Side.SHORT,
            bars_5m=[short_reclaim(), short_rotation(2_000)],
            bars_15m=[supportive_15m(Side.SHORT, 2_000)],
            evaluated_ms=2_000,
        )
    )

    assert result.state == TriggerState.CONFIRMED


def test_short_adverse_close_before_rotation_fails():
    failure = bar(2_000, open=100, high=100.6, low=99.8, close=100.3)
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.RANGE_SHORT,
            side=Side.SHORT,
            bars_5m=[short_reclaim(), failure],
        )
    )

    assert result.state == TriggerState.FAILED


def macro_request(side: Side, bars_5m, bars_15m=None, **kwargs):
    setup_type = (
        ScannerSetupType.MACRO_BREAKOUT_LONG
        if side == Side.LONG
        else ScannerSetupType.MACRO_BREAKOUT_SHORT
    )
    return request(
        setup_type=setup_type,
        side=side,
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        **kwargs,
    )


def test_macro_long_without_retest_waits():
    result = evaluate_lower_timeframe_trigger(
        macro_request(Side.LONG, [bar(1_000, open=102, high=103, low=101, close=102)])
    )

    assert result.state == TriggerState.WAITING


def test_macro_long_band_touch_without_hold_develops():
    touch = bar(1_000, open=100.2, high=100.4, low=99.8, close=100)
    result = evaluate_lower_timeframe_trigger(macro_request(Side.LONG, [touch]))

    assert result.state == TriggerState.DEVELOPING
    assert result.anchor_bar_end_ms is None


def test_macro_long_touch_and_close_above_holds():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    result = evaluate_lower_timeframe_trigger(macro_request(Side.LONG, [hold]))

    assert result.state == TriggerState.RETEST_HELD
    assert result.anchor_bar_end_ms == 1_000


def test_macro_touch_then_later_accepted_close_holds():
    touch = bar(1_000, open=100.2, high=100.4, low=99.8, close=100)
    hold = bar(2_000, open=100.1, high=101, low=100.1, close=100.5)

    result = evaluate_lower_timeframe_trigger(macro_request(Side.LONG, [touch, hold]))

    assert result.state == TriggerState.RETEST_HELD
    assert result.anchor_bar_end_ms == 2_000


def test_macro_long_later_continuation_confirms_with_15m():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.LONG,
            [hold, long_rotation()],
            [supportive_15m(Side.LONG)],
        )
    )

    assert result.state == TriggerState.CONFIRMED
    assert result.trigger_confirmed


def test_macro_long_adverse_close_fails():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    failure = bar(2_000, open=100, high=100.1, low=99.4, close=99.7)
    result = evaluate_lower_timeframe_trigger(
        macro_request(Side.LONG, [hold, failure])
    )

    assert result.state == TriggerState.FAILED


def test_macro_partial_continuation_is_ignored():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.LONG,
            [hold, long_rotation(3_000)],
            [supportive_15m(Side.LONG)],
            evaluated_ms=2_000,
        )
    )

    assert result.state == TriggerState.RETEST_HELD


def test_macro_short_hold_rotation_and_acceptance_confirm():
    hold = bar(1_000, open=100, high=100.2, low=99, close=99.5)
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.SHORT,
            [hold, short_rotation()],
            [supportive_15m(Side.SHORT)],
        )
    )

    assert result.state == TriggerState.CONFIRMED


def test_macro_short_without_retest_waits():
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.SHORT,
            [bar(1_000, open=98, high=99, low=97, close=98)],
        )
    )

    assert result.state == TriggerState.WAITING


def test_macro_short_touch_and_close_below_holds():
    hold = bar(1_000, open=100, high=100.2, low=99, close=99.5)
    result = evaluate_lower_timeframe_trigger(macro_request(Side.SHORT, [hold]))

    assert result.state == TriggerState.RETEST_HELD


def test_macro_short_partial_continuation_is_ignored():
    hold = bar(1_000, open=100, high=100.2, low=99, close=99.5)
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.SHORT,
            [hold, short_rotation(3_000)],
            [supportive_15m(Side.SHORT)],
            evaluated_ms=2_000,
        )
    )

    assert result.state == TriggerState.RETEST_HELD


def test_macro_short_adverse_close_fails():
    hold = bar(1_000, open=100, high=100.2, low=99, close=99.5)
    failure = bar(2_000, open=100, high=100.6, low=99.8, close=100.3)
    result = evaluate_lower_timeframe_trigger(
        macro_request(Side.SHORT, [hold, failure])
    )

    assert result.state == TriggerState.FAILED


@pytest.mark.parametrize(
    ("bars_15m", "expected_acceptance"),
    [
        ([supportive_15m(Side.LONG)], True),
        ([bar(3_000, close=99.5)], False),
        ([], None),
    ],
)
def test_5m_continuation_requires_supportive_15m_for_confirmation(
    bars_15m, expected_acceptance
):
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[long_reclaim(), long_rotation()], bars_15m=bars_15m)
    )

    assert result.local_15m_acceptance is expected_acceptance
    assert result.state == (
        TriggerState.CONFIRMED if expected_acceptance else TriggerState.DEVELOPING
    )
    assert result.trigger_confirmed is bool(expected_acceptance)


def test_partial_15m_bar_cannot_supply_acceptance():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(), long_rotation()],
            bars_15m=[supportive_15m(Side.LONG, 3_000)],
            evaluated_ms=2_000,
        )
    )

    assert result.local_15m_acceptance is None
    assert result.state == TriggerState.DEVELOPING


@pytest.mark.parametrize(
    ("side", "reaction_state", "supportive"),
    [
        (Side.LONG, "sell_absorption", True),
        (Side.LONG, "buy_continuation", True),
        (Side.LONG, "sell_continuation", False),
        (Side.SHORT, "buy_absorption", True),
        (Side.SHORT, "sell_continuation", True),
        (Side.SHORT, "buy_continuation", False),
    ],
)
def test_reaction_support_is_side_specific(side, reaction_state, supportive):
    setup_type = (
        ScannerSetupType.TREND_PULLBACK_LONG
        if side == Side.LONG
        else ScannerSetupType.TREND_PULLBACK_SHORT
    )
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=setup_type,
            side=side,
            bars_5m=[bar(1_000)],
            reaction_state=reaction_state,
        )
    )

    assert result.reaction_state == reaction_state
    assert result.reaction_supportive is supportive
    assert not result.trigger_confirmed


def test_supportive_reaction_alone_does_not_confirm():
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[bar(1_000)], reaction_state="sell_absorption")
    )

    assert result.reaction_supportive is True
    assert result.state == TriggerState.WAITING
    assert not result.trigger_confirmed


def test_no_completed_5m_bars_is_indeterminate():
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[bar(11_000)], evaluated_ms=10_000)
    )

    assert result.state == TriggerState.INDETERMINATE
    assert result.evidence_missing == ["COMPLETED_5M_BARS"]


def test_unsorted_bars_produce_same_result():
    ordered = request(
        bars_5m=[long_reclaim(), long_rotation()],
        bars_15m=[supportive_15m(Side.LONG)],
    )
    unsorted = ordered.model_copy(
        update={"bars_5m": list(reversed(ordered.bars_5m))}
    )

    assert evaluate_lower_timeframe_trigger(ordered) == evaluate_lower_timeframe_trigger(
        unsorted
    )


def test_identical_duplicate_bars_are_collapsed():
    anchor = long_reclaim()
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[anchor, anchor.model_copy(), long_rotation()],
            bars_15m=[supportive_15m(Side.LONG)],
        )
    )

    assert result.state == TriggerState.CONFIRMED


def test_conflicting_duplicate_intervals_are_rejected():
    first = bar(1_000, close=100)
    conflicting = bar(1_000, close=100.5)

    with pytest.raises(ValueError, match="conflicting duplicate"):
        evaluate_lower_timeframe_trigger(request(bars_5m=[first, conflicting]))


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_ms": 1_000, "end_ms": 1_000},
        {"open": 0},
        {"low": 102, "high": 101},
        {"open": 102, "low": 99, "high": 101},
        {"close": 98, "low": 99, "high": 101},
    ],
)
def test_malformed_bars_are_rejected(overrides):
    values = {"start_ms": 0, "end_ms": 1_000, "open": 100, "high": 101, "low": 99, "close": 100}
    values.update(overrides)

    with pytest.raises(ValidationError):
        LowerTimeframeBar(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_level", 0),
        ("reference_level", -1),
        ("retest_tolerance_bps", -1),
        ("failure_tolerance_bps", -1),
    ],
)
def test_invalid_level_or_tolerances_are_rejected(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


def test_side_must_match_typed_setup():
    with pytest.raises(ValidationError, match="side must match setup_type"):
        request(
            setup_type=ScannerSetupType.TREND_PULLBACK_LONG,
            side=Side.SHORT,
        )


def test_result_has_no_numeric_score_or_permission_fields():
    forbidden = {
        "setup_score",
        "confidence_score",
        "entry_permission",
        "probe_permission",
        "add_permission",
        "sizing",
        "leverage",
    }

    assert forbidden.isdisjoint(TriggerResult.model_fields)


def test_engine_has_no_exchange_or_framework_dependency():
    source = getsource(trigger_engine)

    assert "bybit" not in source.lower()
    assert "sqlalchemy" not in source.lower()
    assert "fastapi" not in source.lower()
    assert "place_order" not in source


def test_armed_at_must_be_timezone_aware():
    payload = request().model_dump()
    payload["armed_at"] = datetime(2025, 1, 1)  # noqa: DTZ001 - intentionally naive

    with pytest.raises(ValidationError, match="timezone-aware"):
        TriggerEvaluationRequest.model_validate(payload)


def test_armed_at_cannot_be_after_evaluation():
    with pytest.raises(ValidationError, match="armed_at must not be later"):
        request(armed_ms=2_001, evaluated_ms=2_000)


def test_bar_ending_exactly_at_arm_is_excluded():
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[long_reclaim(1_000)], armed_ms=1_000)
    )

    assert result.state == TriggerState.INDETERMINATE


def test_first_bar_ending_after_arm_is_included():
    result = evaluate_lower_timeframe_trigger(
        request(bars_5m=[long_reclaim(1_001)], armed_ms=1_000)
    )

    assert result.state == TriggerState.RECLAIMED
    assert result.armed_at == datetime.fromtimestamp(1, UTC)


def test_full_pre_arm_long_sequence_is_ignored():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(), long_rotation()],
            bars_15m=[supportive_15m(Side.LONG)],
            armed_ms=2_500,
        )
    )

    assert result.state == TriggerState.INDETERMINATE
    assert not result.trigger_confirmed


def test_pre_arm_long_sequence_with_unrelated_post_arm_bar_waits():
    unrelated = bar(3_000, open=101, high=102, low=100.5, close=101)
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(), long_rotation(), unrelated],
            bars_15m=[supportive_15m(Side.LONG)],
            armed_ms=2_500,
        )
    )

    assert result.state == TriggerState.WAITING


def test_pre_arm_reclaim_cannot_anchor_post_arm_continuation():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(), long_rotation(3_000)],
            bars_15m=[supportive_15m(Side.LONG, 3_000)],
            armed_ms=2_000,
        )
    )

    assert result.state == TriggerState.WAITING
    assert result.anchor_bar_end_ms is None


def test_fully_post_arm_long_sequence_confirms():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(3_000), long_rotation(4_000)],
            bars_15m=[supportive_15m(Side.LONG, 4_000)],
            armed_ms=2_000,
        )
    )

    assert result.state == TriggerState.CONFIRMED


def test_pre_arm_short_reclaim_cannot_anchor_post_arm_continuation():
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.RANGE_SHORT,
            side=Side.SHORT,
            bars_5m=[short_reclaim(), short_rotation(3_000)],
            bars_15m=[supportive_15m(Side.SHORT, 3_000)],
            armed_ms=2_000,
        )
    )

    assert result.state == TriggerState.WAITING
    assert not result.trigger_confirmed


def test_fully_post_arm_short_sequence_confirms_at_evaluation_boundary():
    result = evaluate_lower_timeframe_trigger(
        request(
            setup_type=ScannerSetupType.RANGE_SHORT,
            side=Side.SHORT,
            bars_5m=[short_reclaim(3_000), short_rotation(4_000)],
            bars_15m=[supportive_15m(Side.SHORT, 4_000)],
            armed_ms=2_000,
            evaluated_ms=4_000,
        )
    )

    assert result.state == TriggerState.CONFIRMED


def test_pre_arm_15m_acceptance_is_ignored():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(3_000), long_rotation(4_000)],
            bars_15m=[supportive_15m(Side.LONG, 2_000)],
            armed_ms=2_500,
        )
    )

    assert result.state == TriggerState.DEVELOPING
    assert result.local_15m_acceptance is None


def test_post_arm_15m_acceptance_is_used():
    result = evaluate_lower_timeframe_trigger(
        request(
            bars_5m=[long_reclaim(3_000), long_rotation(4_000)],
            bars_15m=[supportive_15m(Side.LONG, 4_000)],
            armed_ms=2_500,
        )
    )

    assert result.state == TriggerState.CONFIRMED
    assert result.local_15m_acceptance is True


def test_pre_arm_macro_hold_cannot_anchor_post_arm_continuation():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    continuation = bar(3_000, open=101, high=102.5, low=101, close=102)
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.LONG,
            [hold, continuation],
            [supportive_15m(Side.LONG, 3_000)],
            armed_ms=2_000,
        )
    )

    assert result.state == TriggerState.WAITING
    assert not result.trigger_confirmed


def test_failed_macro_hold_is_not_resurrected_by_non_touch_close():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    failure = bar(2_000, open=100, high=100.1, low=99.4, close=99.7)
    unrelated = bar(3_000, open=101, high=102, low=100.8, close=101.5)
    result = evaluate_lower_timeframe_trigger(
        macro_request(Side.LONG, [hold, failure, unrelated])
    )

    assert result.state == TriggerState.FAILED
    assert result.anchor_bar_end_ms == 1_000
    assert result.blocking_reasons == ["TRIGGER_ATTEMPT_FAILED"]


def test_fresh_macro_retest_supersedes_failed_attempt():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    failure = bar(2_000, open=100, high=100.1, low=99.4, close=99.7)
    fresh_hold = bar(3_000, open=100, high=101, low=99.8, close=100.5)
    result = evaluate_lower_timeframe_trigger(
        macro_request(Side.LONG, [hold, failure, fresh_hold])
    )

    assert result.state == TriggerState.RETEST_HELD
    assert result.anchor_bar_end_ms == 3_000


def test_fresh_macro_retest_and_continuation_confirm():
    hold = bar(1_000, open=100, high=101, low=99.8, close=100.5)
    failure = bar(2_000, open=100, high=100.1, low=99.4, close=99.7)
    fresh_hold = bar(3_000, open=100, high=101, low=99.8, close=100.5)
    continuation = bar(4_000, open=101, high=102.5, low=100.5, close=102)
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.LONG,
            [hold, failure, fresh_hold, continuation],
            [supportive_15m(Side.LONG, 4_000)],
        )
    )

    assert result.state == TriggerState.CONFIRMED
    assert result.anchor_bar_end_ms == 3_000


def test_short_failed_macro_attempt_requires_fresh_retest():
    hold = bar(1_000, open=100, high=100.2, low=99, close=99.5)
    failure = bar(2_000, open=100, high=100.6, low=99.8, close=100.3)
    unrelated = bar(3_000, open=99, high=99.2, low=98, close=98.5)
    result = evaluate_lower_timeframe_trigger(
        macro_request(Side.SHORT, [hold, failure, unrelated])
    )

    assert result.state == TriggerState.FAILED


def test_short_fresh_macro_retest_can_confirm_after_failure():
    hold = bar(1_000, open=100, high=100.2, low=99, close=99.5)
    failure = bar(2_000, open=100, high=100.6, low=99.8, close=100.3)
    fresh_hold = bar(3_000, open=100, high=100.2, low=99, close=99.5)
    continuation = bar(4_000, open=99, high=99.5, low=97.5, close=98)
    result = evaluate_lower_timeframe_trigger(
        macro_request(
            Side.SHORT,
            [hold, failure, fresh_hold, continuation],
            [supportive_15m(Side.SHORT, 4_000)],
        )
    )

    assert result.state == TriggerState.CONFIRMED
    assert result.anchor_bar_end_ms == 3_000
