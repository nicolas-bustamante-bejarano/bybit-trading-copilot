from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import ScannerSetupType, ScannerStatus
from trading_copilot.domain.trigger import TriggerArmSource, TriggerReferenceSource, TriggerState
from trading_copilot.persistence.models import (
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TriggerAttemptRow,
    WatchedSetupRow,
)
from trading_copilot.services.trigger_context import (
    DEFAULT_TRIGGER_FAILURE_TOLERANCE_BPS,
    compose_trigger_request,
    resolve_trigger_context,
)
from trading_copilot.services.trigger_engine import evaluate_lower_timeframe_trigger
from trading_copilot.services.trigger_snapshot import build_trigger_snapshot

ARMED_AT = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def range_state(low=95, high=105):
    return {"location": {"range_low": low, "range_high": high}}


def trend_state(*, active="primary", zones=None):
    return {
        "location": {
            "active_zone": active,
            "zones": zones
            if zones is not None
            else [{"name": "primary", "lower": 95, "upper": 100, "enabled": True}],
        }
    }


def macro_state(level=100, side="LONG", structure_id="structure-1", projected=125):
    return {
        "accepted_breakout_level": level,
        "accepted_side": side,
        "accepted_structure_id": structure_id,
        "structure": {"breakout_level": projected},
    }


def watched(
    setup_type=ScannerSetupType.RANGE_LONG,
    *,
    status=ScannerStatus.TRIGGER_ARMED,
    state=None,
    version=2,
    created_at=ARMED_AT - timedelta(hours=1),
):
    return WatchedSetupRow(
        id="setup-1",
        symbol="BTCUSDT",
        setup_type=setup_type.value,
        status=status.value,
        state=state if state is not None else range_state(),
        version=version,
        created_at=created_at,
        updated_at=ARMED_AT + timedelta(hours=5),
        last_evaluated_at=ARMED_AT + timedelta(hours=4),
    )


def watchlist(*, enabled=True, playbooks=None):
    return ScannerWatchlistRow(
        id="watch-1",
        symbol="BTCUSDT",
        enabled=enabled,
        enabled_playbooks=(
            playbooks
            if playbooks is not None
            else ["TREND_PULLBACK", "RANGE", "MACRO_BREAKOUT"]
        ),
        approach_tolerance_bps=Decimal(50),
        retest_tolerance_bps=Decimal("17.5"),
        acceptance_bars=2,
    )


def transition(
    state_after,
    *,
    timestamp=ARMED_AT,
    version=2,
    to_status=ScannerStatus.TRIGGER_ARMED,
    transition_id="transition-1",
):
    return ScannerTransitionRow(
        id=transition_id,
        watched_setup_id="setup-1",
        symbol="BTCUSDT",
        setup_type=ScannerSetupType.RANGE_LONG.value,
        from_status=ScannerStatus.REACTION_DEVELOPING.value,
        to_status=to_status.value,
        timestamp=timestamp,
        state_before={},
        state_after=state_after,
        version=version,
    )


def attempt_for(context, **changes):
    values = {
        "id": "attempt-1",
        "watched_setup_id": context.watched_setup_id,
        "symbol": context.symbol,
        "setup_type": context.setup_type.value,
        "arm_key": context.arm_key,
        "arm_source": context.arm_source.value,
        "arm_transition_id": context.arm_transition_id,
        "armed_at": context.armed_at,
        "reference_level": Decimal(str(context.reference_level)),
        "reference_source": context.reference_source.value,
        "reference_metadata": dict(context.reference_metadata),
        "retest_tolerance_bps": Decimal(str(context.retest_tolerance_bps)),
        "failure_tolerance_bps": Decimal(str(context.failure_tolerance_bps)),
        "state": TriggerState.WAITING.value,
        "result": {},
        "version": 1,
        "first_evaluated_at": ARMED_AT,
        "last_evaluated_at": ARMED_AT,
    }
    values.update(changes)
    return TriggerAttemptRow(**values)


@pytest.mark.parametrize(
    "status",
    [
        ScannerStatus.WATCH,
        ScannerStatus.AT_LOCATION,
        ScannerStatus.REACTION_DEVELOPING,
        ScannerStatus.RETEST_PENDING,
    ],
)
def test_only_trigger_armed_is_eligible(status):
    row = watched(status=status)

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[]
    )

    assert not result.eligible
    assert result.blocking_reason == "NOT_ELIGIBLE"


def test_latest_transition_into_trigger_armed_sets_armed_at():
    older = transition(range_state(90, 110), timestamp=ARMED_AT, version=2)
    latest_at = ARMED_AT + timedelta(hours=2)
    latest = transition(
        range_state(95, 105),
        timestamp=latest_at,
        version=4,
        transition_id="transition-2",
    )

    result = resolve_trigger_context(
        watched=watched(version=4),
        watchlist=watchlist(),
        transitions=[latest, older],
    )

    assert result.eligible
    assert result.armed_at == latest_at
    assert result.arm_source == TriggerArmSource.ARM_TRANSITION
    assert result.reference_level == 95


def test_non_arm_transition_is_ignored():
    other = transition(
        range_state(80, 120),
        timestamp=ARMED_AT + timedelta(hours=3),
        to_status=ScannerStatus.WATCH,
    )
    armed = transition(range_state(), timestamp=ARMED_AT)

    result = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[other, armed]
    )

    assert result.armed_at == ARMED_AT


def test_first_observation_uses_created_at_without_inventing_transition():
    row = watched(version=1, state=range_state(94, 106))

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[]
    )

    assert result.eligible
    assert result.armed_at == row.created_at
    assert result.arm_source == TriggerArmSource.FIRST_OBSERVATION_BASELINE
    assert result.reference_level == 94


def test_later_version_without_arm_transition_is_blocked():
    result = resolve_trigger_context(
        watched=watched(version=3), watchlist=watchlist(), transitions=[]
    )

    assert not result.eligible
    assert result.blocking_reason == "ARMED_AT_UNRESOLVED"


def test_mutable_row_timestamps_do_not_replace_arm_transition_time():
    row = watched()
    row.updated_at = ARMED_AT + timedelta(days=10)
    row.last_evaluated_at = ARMED_AT + timedelta(days=11)

    result = resolve_trigger_context(
        watched=row,
        watchlist=watchlist(),
        transitions=[transition(range_state())],
    )

    assert result.armed_at == ARMED_AT


def test_generic_reference_is_frozen_from_transition_state():
    row = watched(state=range_state(70, 130))

    result = resolve_trigger_context(
        watched=row,
        watchlist=watchlist(),
        transitions=[transition(range_state(95, 105))],
    )

    assert result.reference_level == 95
    assert result.armed_state == range_state(95, 105)


def test_macro_reference_is_frozen_from_transition_acceptance_memory():
    setup_type = ScannerSetupType.MACRO_BREAKOUT_LONG
    row = watched(setup_type, state=macro_state(130), version=3)
    armed = transition(macro_state(100, projected=110), version=3)
    armed.setup_type = setup_type.value

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    assert result.reference_level == 100
    assert result.reference_source == TriggerReferenceSource.ACCEPTED_BREAKOUT_LEVEL
    assert result.reference_metadata == {"accepted_structure_id": "structure-1"}


@pytest.mark.parametrize(
    ("setup_type", "expected", "source"),
    [
        (ScannerSetupType.RANGE_LONG, 95, TriggerReferenceSource.RANGE_LOW),
        (ScannerSetupType.RANGE_SHORT, 105, TriggerReferenceSource.RANGE_HIGH),
    ],
)
def test_range_uses_adverse_boundary(setup_type, expected, source):
    row = watched(setup_type)
    armed = transition(range_state())
    armed.setup_type = setup_type.value

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    assert result.reference_level == expected
    assert result.reference_source == source


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), "95"])
def test_range_missing_or_malformed_boundary_is_blocked(value):
    result = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(),
        transitions=[transition(range_state(value, 105))],
    )

    assert not result.eligible
    assert result.blocking_reason == "TRIGGER_REFERENCE_REQUIRED"


@pytest.mark.parametrize(
    ("setup_type", "expected", "source"),
    [
        (
            ScannerSetupType.TREND_PULLBACK_LONG,
            95,
            TriggerReferenceSource.FIB_ZONE_LOWER,
        ),
        (
            ScannerSetupType.TREND_PULLBACK_SHORT,
            100,
            TriggerReferenceSource.FIB_ZONE_UPPER,
        ),
    ],
)
def test_trend_uses_active_zone_adverse_boundary(setup_type, expected, source):
    row = watched(setup_type, state=trend_state())
    armed = transition(trend_state())
    armed.setup_type = setup_type.value

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    assert result.reference_level == expected
    assert result.reference_source == source
    assert result.reference_metadata == {"active_zone": "primary"}


@pytest.mark.parametrize(
    "state",
    [
        {"location": {"zones": []}},
        trend_state(active="missing"),
        trend_state(
            zones=[
                {"name": "primary", "lower": 95, "upper": 100, "enabled": True},
                {"name": "primary", "lower": 94, "upper": 99, "enabled": True},
            ]
        ),
        trend_state(
            zones=[{"name": "primary", "lower": 95, "upper": 100, "enabled": False}]
        ),
        trend_state(
            zones=[{"name": "primary", "lower": "95", "upper": 100, "enabled": True}]
        ),
    ],
)
def test_trend_malformed_active_zone_is_blocked(state):
    row = watched(ScannerSetupType.TREND_PULLBACK_LONG, state=state)
    armed = transition(state)
    armed.setup_type = row.setup_type

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    assert not result.eligible
    assert result.blocking_reason == "TRIGGER_REFERENCE_REQUIRED"


@pytest.mark.parametrize(
    ("setup_type", "side"),
    [
        (ScannerSetupType.MACRO_BREAKOUT_LONG, "LONG"),
        (ScannerSetupType.MACRO_BREAKOUT_SHORT, "SHORT"),
    ],
)
def test_macro_returns_exact_accepted_breakout_level(setup_type, side):
    row = watched(setup_type, state=macro_state(100, side))
    armed = transition(macro_state(100, side, projected=140))
    armed.setup_type = setup_type.value

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    assert result.reference_level == 100
    assert result.side == (Side.LONG if side == "LONG" else Side.SHORT)


@pytest.mark.parametrize(
    "state",
    [
        macro_state(None),
        macro_state(100, side="SHORT"),
        macro_state(100, structure_id=""),
    ],
)
def test_macro_incomplete_acceptance_memory_is_blocked(state):
    row = watched(ScannerSetupType.MACRO_BREAKOUT_LONG, state=state)
    armed = transition(state)
    armed.setup_type = row.setup_type

    result = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    assert not result.eligible
    assert result.blocking_reason == "ACCEPTED_BREAKOUT_REFERENCE_REQUIRED"


def test_watchlist_tolerances_are_exposed_with_named_failure_default():
    result = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(),
        transitions=[transition(range_state())],
    )

    assert result.retest_tolerance_bps == 17.5
    assert result.failure_tolerance_bps == DEFAULT_TRIGGER_FAILURE_TOLERANCE_BPS


def test_disabled_watchlist_blocks_historical_armed_setup():
    result = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(enabled=False),
        transitions=[transition(range_state())],
    )

    assert not result.eligible
    assert result.blocking_reason == "WATCHLIST_DISABLED"


@pytest.mark.parametrize(
    "setup_type",
    [ScannerSetupType.TREND_PULLBACK_LONG, ScannerSetupType.TREND_PULLBACK_SHORT],
)
def test_family_playbook_enables_both_directions(setup_type):
    state = trend_state()
    row = watched(setup_type, state=state)
    armed = transition(state)
    armed.setup_type = setup_type.value

    result = resolve_trigger_context(
        watched=row,
        watchlist=watchlist(playbooks=["TREND_PULLBACK"]),
        transitions=[armed],
    )

    assert result.eligible


def test_exact_playbook_enables_only_exact_setup():
    long_result = resolve_trigger_context(
        watched=watched(ScannerSetupType.RANGE_LONG),
        watchlist=watchlist(playbooks=["RANGE_LONG"]),
        transitions=[transition(range_state())],
    )
    short_row = watched(ScannerSetupType.RANGE_SHORT)
    short_transition = transition(range_state())
    short_transition.setup_type = short_row.setup_type
    short_result = resolve_trigger_context(
        watched=short_row,
        watchlist=watchlist(playbooks=["RANGE_LONG"]),
        transitions=[short_transition],
    )

    assert long_result.eligible
    assert not short_result.eligible
    assert short_result.blocking_reason == "SETUP_DISABLED"


def test_removed_family_blocks_historical_armed_setup():
    result = resolve_trigger_context(
        watched=watched(ScannerSetupType.RANGE_LONG),
        watchlist=watchlist(playbooks=["TREND_PULLBACK"]),
        transitions=[transition(range_state())],
    )

    assert not result.eligible
    assert result.blocking_reason == "SETUP_DISABLED"


def test_malformed_enabled_playbooks_blocks_safely():
    result = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(playbooks=["UNKNOWN_PLAYBOOK"]),
        transitions=[transition(range_state())],
    )

    assert not result.eligible
    assert result.blocking_reason == "TRIGGER_CONFIG_INVALID"


def test_transition_arm_key_is_stable():
    armed = transition(range_state(), transition_id="arm-transition")
    first = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[armed]
    )
    second = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[armed]
    )

    assert first.arm_key == second.arm_key == "TRANSITION:arm-transition"
    assert first.arm_transition_id == "arm-transition"


def test_baseline_arm_key_is_stable():
    row = watched(version=1)
    first = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[]
    )
    second = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[]
    )

    assert first.arm_key == second.arm_key
    assert first.arm_key == f"BASELINE:{row.id}:{row.created_at.isoformat()}"
    assert first.arm_transition_id is None


def test_latest_rearm_gets_new_transition_identity():
    first_arm = transition(
        range_state(90, 110),
        timestamp=ARMED_AT,
        transition_id="arm-a",
        version=2,
    )
    disarm = transition(
        range_state(90, 110),
        timestamp=ARMED_AT + timedelta(hours=1),
        transition_id="disarm",
        version=3,
        to_status=ScannerStatus.WATCH,
    )
    second_arm = transition(
        range_state(95, 105),
        timestamp=ARMED_AT + timedelta(hours=2),
        transition_id="arm-b",
        version=4,
    )

    result = resolve_trigger_context(
        watched=watched(version=4),
        watchlist=watchlist(),
        transitions=[first_arm, disarm, second_arm],
    )

    assert result.arm_key == "TRANSITION:arm-b"
    assert result.arm_transition_id == "arm-b"
    assert result.reference_level == 95


def test_mutable_row_timestamps_do_not_affect_arm_key():
    row = watched()
    armed = transition(range_state(), transition_id="fixed-arm")
    before = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )
    row.updated_at = row.updated_at + timedelta(days=50)
    row.last_evaluated_at = row.last_evaluated_at + timedelta(days=60)
    after = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    assert before.arm_key == after.arm_key == "TRANSITION:fixed-arm"


def test_existing_attempt_freezes_baseline_reference_and_tolerances():
    row = watched(version=1, state=range_state(100, 110))
    initial = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[]
    )
    existing = attempt_for(initial)
    row.state = range_state(101, 110)
    changed_watchlist = watchlist()
    changed_watchlist.retest_tolerance_bps = Decimal(50)

    resolved = resolve_trigger_context(
        watched=row,
        watchlist=changed_watchlist,
        transitions=[],
        existing_attempt=existing,
    )

    assert resolved.eligible
    assert resolved.reference_level == 100
    assert resolved.retest_tolerance_bps == 17.5


def test_matching_transition_attempt_ignores_mutable_watched_state():
    armed = transition(range_state(100, 110), transition_id="arm-a")
    initial = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[armed]
    )
    existing = attempt_for(
        initial,
        failure_tolerance_bps=Decimal(30),
    )
    mutable_row = watched(state=range_state(101, 110))
    changed_watchlist = watchlist()
    changed_watchlist.retest_tolerance_bps = Decimal(50)

    resolved = resolve_trigger_context(
        watched=mutable_row,
        watchlist=changed_watchlist,
        transitions=[armed],
        existing_attempt=existing,
    )

    assert resolved.eligible
    assert resolved.reference_level == 100
    assert resolved.retest_tolerance_bps == 17.5
    assert resolved.failure_tolerance_bps == 30


def test_transition_attempt_reference_level_mismatch_blocks():
    armed = transition(range_state(100, 110), transition_id="arm-a")
    initial = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[armed]
    )

    resolved = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(),
        transitions=[armed],
        existing_attempt=attempt_for(initial, reference_level=Decimal(99)),
    )

    assert not resolved.eligible
    assert resolved.blocking_reason == "TRIGGER_ATTEMPT_CONTEXT_INVALID"


def test_transition_attempt_reference_source_mismatch_blocks():
    armed = transition(range_state(100, 110), transition_id="arm-a")
    initial = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[armed]
    )

    resolved = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(),
        transitions=[armed],
        existing_attempt=attempt_for(
            initial, reference_source=TriggerReferenceSource.RANGE_HIGH.value
        ),
    )

    assert not resolved.eligible
    assert resolved.blocking_reason == "TRIGGER_ATTEMPT_CONTEXT_INVALID"


def test_transition_attempt_reference_metadata_mismatch_blocks():
    state = trend_state(active="primary")
    row = watched(ScannerSetupType.TREND_PULLBACK_LONG, state=state)
    armed = transition(state, transition_id="arm-a")
    armed.setup_type = row.setup_type
    initial = resolve_trigger_context(
        watched=row, watchlist=watchlist(), transitions=[armed]
    )

    resolved = resolve_trigger_context(
        watched=row,
        watchlist=watchlist(),
        transitions=[armed],
        existing_attempt=attempt_for(
            initial, reference_metadata={"active_zone": "deep"}
        ),
    )

    assert not resolved.eligible
    assert resolved.blocking_reason == "TRIGGER_ATTEMPT_CONTEXT_INVALID"


def test_existing_attempt_cannot_bypass_malformed_transition_snapshot():
    armed = transition(range_state(100, 110), transition_id="arm-a")
    initial = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[armed]
    )
    armed.state_after = {"location": {"range_low": None, "range_high": 110}}

    resolved = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(),
        transitions=[armed],
        existing_attempt=attempt_for(initial),
    )

    assert not resolved.eligible
    assert resolved.blocking_reason == "TRIGGER_ATTEMPT_CONTEXT_INVALID"


def test_old_attempt_is_ignored_after_rearm_and_new_config_is_used():
    first_arm = transition(range_state(90, 110), transition_id="arm-a", version=2)
    first = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[first_arm]
    )
    old_attempt = attempt_for(first)
    second_arm = transition(
        range_state(98, 108),
        timestamp=ARMED_AT + timedelta(hours=1),
        transition_id="arm-b",
        version=4,
    )
    changed_watchlist = watchlist()
    changed_watchlist.retest_tolerance_bps = Decimal(50)

    resolved = resolve_trigger_context(
        watched=watched(version=4),
        watchlist=changed_watchlist,
        transitions=[first_arm, second_arm],
        existing_attempt=old_attempt,
    )

    assert resolved.arm_key == "TRANSITION:arm-b"
    assert resolved.reference_level == 98
    assert resolved.retest_tolerance_bps == 50


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("arm_source", "CORRUPT"),
        ("arm_transition_id", "wrong-transition"),
        ("reference_level", Decimal(0)),
        ("reference_metadata", ["not", "a", "mapping"]),
    ],
)
def test_malformed_current_attempt_blocks_safely(field, value):
    armed = transition(range_state(), transition_id="arm-a")
    initial = resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[armed]
    )

    resolved = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(),
        transitions=[armed],
        existing_attempt=attempt_for(initial, **{field: value}),
    )

    assert not resolved.eligible
    assert resolved.blocking_reason == "TRIGGER_ATTEMPT_CONTEXT_INVALID"


def test_composer_rejects_ineligible_context():
    context = resolve_trigger_context(
        watched=watched(status=ScannerStatus.WATCH),
        watchlist=watchlist(),
        transitions=[],
    )

    with pytest.raises(ValueError, match="not eligible"):
        compose_trigger_request(context, object())


@pytest.mark.asyncio
async def test_context_snapshot_composition_fits_existing_evaluator():
    base_ms = int(ARMED_AT.timestamp() * 1000)

    def row(start_ms, open_, high, low, close):
        return [str(start_ms), str(open_), str(high), str(low), str(close), "10"]

    class Client:
        async def klines(self, symbol, interval, limit):
            if interval == "5":
                return [
                    row(base_ms, 100, 101, 94, 100.5),
                    row(base_ms + 300_000, 100.5, 102, 100, 101.5),
                ]
            return [row(base_ms, 100, 101, 99, 100.5)]

    context = resolve_trigger_context(
        watched=watched(),
        watchlist=watchlist(),
        transitions=[transition(range_state(95, 105))],
    )
    snapshot = await build_trigger_snapshot(
        "BTCUSDT",
        evaluated_at=ARMED_AT + timedelta(minutes=15),
        client=Client(),
    )
    trigger_request = compose_trigger_request(context, snapshot)
    result = evaluate_lower_timeframe_trigger(trigger_request)

    assert trigger_request.armed_at == ARMED_AT
    assert trigger_request.reference_level == 95
    assert result.state == TriggerState.CONFIRMED
    assert result.trigger_confirmed
