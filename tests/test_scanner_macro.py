from datetime import UTC, datetime, timedelta

import pytest

from trading_copilot.domain.scanner import ScannerStatus
from trading_copilot.services.scanner_macro import evaluate_macro_breakout

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def evaluate(side="LONG", **overrides):
    values = {
        "side": side,
        "current_price": 90,
        "breakout_level": 100,
        "approach_tolerance_bps": 100,
        "retest_tolerance_bps": 50,
        "acceptance_bars": 2,
        "completed_1h_closes": [],
        "previous_status": None,
        "previous_state": None,
        "structure_id": "structure-a",
        "structure_metadata": {"label": "weekly resistance"},
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return evaluate_macro_breakout(**values)


def accepted_state(side="LONG", structure_id="structure-a", level=100):
    return {
        "accepted_at": NOW.isoformat(),
        "accepted_breakout_level": level,
        "accepted_structure_id": structure_id,
        "accepted_close_count": 2,
        "acceptance_bars": 2,
        "accepted_side": side,
    }


def test_long_far_below_level_is_watch():
    assert evaluate().status == ScannerStatus.WATCH


def test_long_near_below_level_is_approaching():
    assert evaluate(current_price=99.5).status == ScannerStatus.APPROACHING_BREAKOUT


def test_long_exactly_at_level_is_not_breakout_attempt():
    result = evaluate(current_price=100, approach_tolerance_bps=0)

    assert result.status == ScannerStatus.APPROACHING_BREAKOUT
    assert result.status != ScannerStatus.BREAKOUT_ATTEMPT


def test_long_live_price_above_without_qualifying_close_is_attempt():
    assert evaluate(current_price=101, completed_1h_closes=[99]).status == ScannerStatus.BREAKOUT_ATTEMPT


def test_long_one_completed_close_above_is_acceptance_pending():
    result = evaluate(current_price=101, completed_1h_closes=[99, 101])

    assert result.status == ScannerStatus.ACCEPTANCE_PENDING
    assert result.qualifying_close_count == 1
    assert result.required_acceptance_bars == 2
    assert result.breakout_level == 100


def test_long_two_consecutive_completed_closes_accept():
    assert evaluate(completed_1h_closes=[101, 102]).status == ScannerStatus.BREAKOUT_ACCEPTED


def test_long_non_consecutive_closes_do_not_accept():
    result = evaluate(completed_1h_closes=[101, 99, 102])

    assert result.status == ScannerStatus.ACCEPTANCE_PENDING
    assert result.qualifying_close_count == 1


def test_long_three_consecutive_closes_satisfy_two_bar_acceptance():
    result = evaluate(completed_1h_closes=[101, 102, 103])

    assert result.status == ScannerStatus.BREAKOUT_ACCEPTED
    assert result.qualifying_close_count == 3


def test_first_acceptance_cycle_is_visible_before_post_acceptance_state():
    first = evaluate(current_price=120, completed_1h_closes=[101, 102])

    assert first.status == ScannerStatus.BREAKOUT_ACCEPTED
    assert first.accepted_at == NOW.isoformat()
    assert first.state["accepted_structure_id"] == "structure-a"


def test_long_accepted_next_cycle_away_is_retest_pending():
    result = evaluate(
        current_price=110,
        previous_status=ScannerStatus.BREAKOUT_ACCEPTED,
        previous_state=accepted_state(),
        evaluated_at=NOW + timedelta(hours=1),
    )

    assert result.status == ScannerStatus.RETEST_PENDING
    assert result.accepted_at == NOW.isoformat()


def test_long_accepted_next_cycle_near_is_trigger_armed():
    result = evaluate(
        current_price=100.5,
        previous_status=ScannerStatus.RETEST_PENDING,
        previous_state=accepted_state(),
    )

    assert result.status == ScannerStatus.TRIGGER_ARMED
    assert result.next_conditions == ["await lower-timeframe trigger engine"]


def test_short_far_above_level_is_watch():
    assert evaluate("SHORT", current_price=110).status == ScannerStatus.WATCH


def test_short_near_above_level_is_approaching():
    assert evaluate("SHORT", current_price=100.5).status == ScannerStatus.APPROACHING_BREAKOUT


def test_short_exactly_at_level_is_not_breakout_attempt():
    result = evaluate("SHORT", current_price=100, approach_tolerance_bps=0)

    assert result.status == ScannerStatus.APPROACHING_BREAKOUT
    assert result.status != ScannerStatus.BREAKOUT_ATTEMPT


def test_short_live_price_below_without_qualifying_close_is_attempt():
    assert evaluate("SHORT", current_price=99, completed_1h_closes=[101]).status == ScannerStatus.BREAKOUT_ATTEMPT


def test_short_one_completed_close_below_is_acceptance_pending():
    result = evaluate("SHORT", current_price=99, completed_1h_closes=[101, 99])

    assert result.status == ScannerStatus.ACCEPTANCE_PENDING
    assert result.qualifying_close_count == 1


def test_short_two_consecutive_completed_closes_accept():
    assert evaluate("SHORT", completed_1h_closes=[99, 98]).status == ScannerStatus.BREAKOUT_ACCEPTED


def test_short_non_consecutive_closes_do_not_accept():
    result = evaluate("SHORT", completed_1h_closes=[99, 101, 98])

    assert result.status == ScannerStatus.ACCEPTANCE_PENDING
    assert result.qualifying_close_count == 1


def test_short_accepted_next_cycle_away_is_retest_pending():
    result = evaluate(
        "SHORT",
        current_price=90,
        previous_status=ScannerStatus.BREAKOUT_ACCEPTED,
        previous_state=accepted_state("SHORT"),
    )

    assert result.status == ScannerStatus.RETEST_PENDING


def test_short_accepted_next_cycle_near_is_trigger_armed():
    result = evaluate(
        "SHORT",
        current_price=99.5,
        previous_status=ScannerStatus.RETEST_PENDING,
        previous_state=accepted_state("SHORT"),
    )

    assert result.status == ScannerStatus.TRIGGER_ARMED


def test_same_structure_retains_acceptance_memory():
    result = evaluate(
        current_price=110,
        previous_status=ScannerStatus.TRIGGER_ARMED,
        previous_state=accepted_state(),
    )

    assert result.status == ScannerStatus.RETEST_PENDING
    assert result.state == accepted_state()


def test_new_structure_does_not_inherit_acceptance_memory():
    result = evaluate(
        current_price=110,
        structure_id="structure-b",
        previous_status=ScannerStatus.BREAKOUT_ACCEPTED,
        previous_state=accepted_state(),
    )

    assert result.status == ScannerStatus.BREAKOUT_ATTEMPT
    assert result.accepted_at is None


def test_changed_level_does_not_inherit_acceptance_memory():
    result = evaluate(
        current_price=111,
        breakout_level=105,
        structure_id="structure-b",
        previous_status=ScannerStatus.BREAKOUT_ACCEPTED,
        previous_state=accepted_state(),
    )

    assert result.status == ScannerStatus.BREAKOUT_ATTEMPT


@pytest.mark.parametrize(
    "malformed",
    [
        {},
        {"accepted_structure_id": "structure-a"},
        {**accepted_state(), "accepted_close_count": "2"},
        {**accepted_state(), "accepted_side": "SHORT"},
    ],
)
def test_malformed_previous_acceptance_does_not_create_false_memory(malformed):
    result = evaluate(
        current_price=110,
        previous_status=ScannerStatus.BREAKOUT_ACCEPTED,
        previous_state=malformed,
    )

    assert result.status == ScannerStatus.BREAKOUT_ATTEMPT
    assert result.accepted_at is None


def test_non_mapping_previous_state_is_rejected():
    with pytest.raises(ValueError, match="previous_state"):
        evaluate(previous_state=["not", "a", "mapping"])


def test_partial_live_bar_is_not_counted_as_completed_close():
    result = evaluate(current_price=102, completed_1h_closes=[101])

    assert result.status == ScannerStatus.ACCEPTANCE_PENDING
    assert result.qualifying_close_count == 1


def test_acceptance_bars_one_accepts_first_qualifying_close():
    assert evaluate(acceptance_bars=1, completed_1h_closes=[101]).status == ScannerStatus.BREAKOUT_ACCEPTED


def test_acceptance_bars_three_requires_three_consecutive_closes():
    pending = evaluate(acceptance_bars=3, completed_1h_closes=[101, 102])
    accepted = evaluate(acceptance_bars=3, completed_1h_closes=[101, 102, 103])

    assert pending.status == ScannerStatus.ACCEPTANCE_PENDING
    assert accepted.status == ScannerStatus.BREAKOUT_ACCEPTED


def test_custom_approach_tolerance_controls_approaching_state():
    outside = evaluate(current_price=99, approach_tolerance_bps=50)
    inside = evaluate(current_price=99, approach_tolerance_bps=101)

    assert outside.status == ScannerStatus.WATCH
    assert inside.status == ScannerStatus.APPROACHING_BREAKOUT


def test_custom_retest_tolerance_controls_trigger_armed_state():
    narrow = evaluate(
        current_price=101,
        retest_tolerance_bps=50,
        previous_status=ScannerStatus.RETEST_PENDING,
        previous_state=accepted_state(),
    )
    wide = evaluate(
        current_price=101,
        retest_tolerance_bps=100,
        previous_status=ScannerStatus.RETEST_PENDING,
        previous_state=accepted_state(),
    )

    assert narrow.status == ScannerStatus.RETEST_PENDING
    assert wide.status == ScannerStatus.TRIGGER_ARMED


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("current_price", 0, "current_price"),
        ("breakout_level", 0, "breakout_level"),
        ("acceptance_bars", 0, "acceptance_bars"),
        ("approach_tolerance_bps", -1, "tolerances"),
        ("retest_tolerance_bps", -1, "tolerances"),
    ],
)
def test_invalid_configuration_is_rejected(field, value, message):
    with pytest.raises(ValueError, match=message):
        evaluate(**{field: value})
