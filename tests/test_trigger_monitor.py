import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import trading_copilot.main as main_module
import trading_copilot.services.trigger_monitor as monitor_module
import trading_copilot.services.trigger_runtime as runtime_module
from trading_copilot.config import Settings
from trading_copilot.domain.scanner import ScannerSetupType, ScannerStatus
from trading_copilot.domain.trigger import (
    LowerTimeframeBar,
    LowerTimeframeTriggerSnapshot,
    TriggerArmSource,
    TriggerReferenceSource,
    TriggerState,
)
from trading_copilot.persistence.models import (
    Base,
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TriggerAttemptRow,
    TriggerTransitionRow,
    WatchedSetupRow,
)
from trading_copilot.services.trigger_context import resolve_trigger_context
from trading_copilot.services.trigger_monitor import TriggerMonitor
from trading_copilot.services.trigger_runtime import (
    TriggerCandidateOutcome,
    _lock_prerequisites,
    evaluate_current_trigger_candidate,
    snapshot_covers_context,
)

ARMED_AT = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
EVALUATED_AT = ARMED_AT + timedelta(minutes=20)


@pytest_asyncio.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trigger-monitor.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        await engine.dispose()


def watchlist(symbol="BTCUSDT", *, enabled=True, playbooks=None):
    return ScannerWatchlistRow(
        id=f"watch-{symbol}",
        symbol=symbol,
        enabled=enabled,
        enabled_playbooks=playbooks or ["RANGE", "TREND_PULLBACK", "MACRO_BREAKOUT"],
        approach_tolerance_bps=Decimal(50),
        retest_tolerance_bps=Decimal(25),
        acceptance_bars=2,
    )


def state_for(setup_type):
    if setup_type == ScannerSetupType.RANGE_LONG:
        return {"location": {"range_low": 100, "range_high": 110}}
    if setup_type == ScannerSetupType.TREND_PULLBACK_LONG:
        return {
            "location": {
                "active_zone": "primary",
                "zones": [
                    {"name": "primary", "lower": 100, "upper": 105, "enabled": True}
                ],
            }
        }
    return {
        "accepted_breakout_level": 100,
        "accepted_side": "LONG",
        "accepted_structure_id": "structure-1",
    }


def watched(setup_type=ScannerSetupType.RANGE_LONG, *, symbol="BTCUSDT", status=None):
    return WatchedSetupRow(
        id=f"setup-{symbol}-{setup_type.value}",
        symbol=symbol,
        setup_type=setup_type.value,
        status=(status or ScannerStatus.TRIGGER_ARMED).value,
        state=state_for(setup_type),
        version=1,
        created_at=ARMED_AT,
    )


def bar(start_ms, minutes, *, low=101, close=101):
    return LowerTimeframeBar(
        start_ms=start_ms,
        end_ms=start_ms + minutes * 60_000,
        open=101,
        high=102,
        low=low,
        close=close,
        volume=10,
    )


def snapshot(symbol="BTCUSDT", *, start_5m=None, start_15m=None, evaluated_at=EVALUATED_AT):
    armed_ms = int(ARMED_AT.timestamp() * 1000)
    return LowerTimeframeTriggerSnapshot(
        symbol=symbol,
        evaluated_at=evaluated_at,
        bars_5m=[bar(start_5m if start_5m is not None else armed_ms, 5)],
        bars_15m=[bar(start_15m if start_15m is not None else armed_ms, 15)],
        data_status="PARTIAL",
    )


def monitor(sessions, builder, *, enabled=True, concurrency=4):
    return TriggerMonitor(
        enabled=enabled,
        interval_seconds=15,
        concurrency=concurrency,
        sessions=sessions,
        build_snapshot=builder,
    )


async def seed(session, *setups, list_row=None):
    session.add(list_row or watchlist(setups[0].symbol if setups else "BTCUSDT"))
    session.add_all(setups)
    await session.commit()


@pytest.mark.asyncio
async def test_final_runtime_queries_lock_watchlist_then_watched_row():
    setup = watched()
    list_row = watchlist()

    class RecordingSession:
        def __init__(self):
            self.statements = []
            self.results = iter(["BTCUSDT", list_row, setup])

        async def scalar(self, statement):
            self.statements.append(statement)
            return next(self.results)

    session = RecordingSession()
    locked_setup, locked_watchlist = await _lock_prerequisites(session, setup.id)

    assert locked_setup is setup
    assert locked_watchlist is list_row
    assert session.statements[0]._for_update_arg is None
    assert session.statements[1]._for_update_arg is not None
    assert session.statements[2]._for_update_arg is not None


@pytest.mark.asyncio
async def test_discovers_only_armed_and_no_candidates_make_no_snapshot_call(sessions):
    calls = []

    async def builder(symbol, evaluated_at):
        calls.append(symbol)
        return snapshot(symbol, evaluated_at=evaluated_at)

    async with sessions() as session:
        await seed(
            session,
            watched(status=ScannerStatus.WATCH),
        )
    item = monitor(sessions, builder)
    await item.run_cycle()

    assert item.armed_candidate_count == 0
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "list_row",
    [watchlist(enabled=False), watchlist(playbooks=["TREND_PULLBACK"])],
)
async def test_disabled_or_removed_setup_is_not_evaluated(sessions, list_row):
    calls = []

    async def builder(symbol, evaluated_at):
        calls.append(symbol)
        return snapshot(symbol, evaluated_at=evaluated_at)

    async with sessions() as session:
        await seed(session, watched(), list_row=list_row)
    item = monitor(sessions, builder)
    await item.run_cycle()

    assert calls == []


@pytest.mark.asyncio
async def test_family_and_exact_playbooks_preserve_context_semantics(sessions, monkeypatch):
    supplied = []

    async def fake_runtime(session, **kwargs):
        supplied.append(kwargs["watched_setup_id"])
        return TriggerCandidateOutcome(evaluated=True, persisted=True)

    monkeypatch.setattr(monitor_module, "evaluate_current_trigger_candidate", fake_runtime)
    async with sessions() as session:
        await seed(
            session,
            watched(ScannerSetupType.RANGE_LONG),
            watched(ScannerSetupType.TREND_PULLBACK_LONG),
            list_row=watchlist(playbooks=["RANGE_LONG", "TREND_PULLBACK"]),
        )
    item = monitor(sessions, lambda symbol, evaluated_at: asyncio.sleep(0, result=snapshot(symbol)))
    await item.run_cycle()

    assert len(supplied) == 2


@pytest.mark.asyncio
async def test_three_setups_share_exactly_one_symbol_snapshot(sessions, monkeypatch):
    built = []
    supplied = []

    async def builder(symbol, evaluated_at):
        value = snapshot(symbol, evaluated_at=evaluated_at)
        built.append(value)
        return value

    async def fake_runtime(session, **kwargs):
        supplied.append(kwargs["snapshot"])
        return TriggerCandidateOutcome(evaluated=True, persisted=True)

    monkeypatch.setattr(monitor_module, "evaluate_current_trigger_candidate", fake_runtime)
    async with sessions() as session:
        await seed(
            session,
            watched(ScannerSetupType.RANGE_LONG),
            watched(ScannerSetupType.TREND_PULLBACK_LONG),
            watched(ScannerSetupType.MACRO_BREAKOUT_LONG),
        )
    item = monitor(sessions, builder)
    await item.run_cycle()

    assert len(built) == 1
    assert len(supplied) == 3
    assert all(value is built[0] for value in supplied)
    assert item.evaluation_count == item.persisted_count == 3


@pytest.mark.asyncio
async def test_two_symbols_get_one_snapshot_each(sessions, monkeypatch):
    calls = []

    async def builder(symbol, evaluated_at):
        calls.append(symbol)
        return snapshot(symbol, evaluated_at=evaluated_at)

    async def fake_runtime(session, **kwargs):
        return TriggerCandidateOutcome(evaluated=True, persisted=True)

    monkeypatch.setattr(monitor_module, "evaluate_current_trigger_candidate", fake_runtime)
    async with sessions() as session:
        session.add_all(
            [watchlist("BTCUSDT"), watchlist("ETHUSDT"), watched(), watched(symbol="ETHUSDT")]
        )
        await session.commit()
    item = monitor(sessions, builder)
    await item.run_cycle()

    assert sorted(calls) == ["BTCUSDT", "ETHUSDT"]


async def add_attempt(session, setup, *, state=TriggerState.CONFIRMED):
    current_watchlist = await session.scalar(
        select(ScannerWatchlistRow).where(ScannerWatchlistRow.symbol == setup.symbol)
    )
    context = resolve_trigger_context(
        watched=setup, watchlist=current_watchlist, transitions=[]
    )
    attempt = TriggerAttemptRow(
        watched_setup_id=setup.id,
        symbol=setup.symbol,
        setup_type=setup.setup_type,
        arm_key=context.arm_key,
        arm_source=TriggerArmSource.FIRST_OBSERVATION_BASELINE.value,
        arm_transition_id=None,
        armed_at=context.armed_at,
        reference_level=Decimal(str(context.reference_level)),
        reference_source=TriggerReferenceSource.RANGE_LOW.value,
        reference_metadata={},
        retest_tolerance_bps=Decimal(25),
        failure_tolerance_bps=Decimal(25),
        state=state.value,
        result={"preserved": True},
        version=3,
        first_evaluated_at=EVALUATED_AT,
        last_evaluated_at=EVALUATED_AT,
    )
    session.add(attempt)
    await session.commit()
    return attempt


@pytest.mark.asyncio
async def test_confirmed_current_arm_is_latched_without_snapshot_or_mutation(sessions):
    setup = watched()
    async with sessions() as session:
        await seed(session, setup)
        attempt = await add_attempt(session, setup)
        attempt_id = attempt.id
    calls = []

    async def builder(symbol, evaluated_at):
        calls.append(symbol)
        return snapshot(symbol)

    item = monitor(sessions, builder)
    await item.run_cycle()
    async with sessions() as session:
        reloaded = await session.get(TriggerAttemptRow, attempt_id)
        transition_count = await session.scalar(select(func.count(TriggerTransitionRow.id)))

    assert calls == []
    assert item.terminal_count == 1
    assert item.last_success_at == item.last_cycle_started_at
    assert reloaded.version == 3
    assert reloaded.result == {"preserved": True}
    assert reloaded.last_evaluated_at.replace(tzinfo=UTC) == EVALUATED_AT
    assert transition_count == 0


@pytest.mark.asyncio
async def test_failed_attempt_is_re_evaluated_and_persisted(sessions):
    setup = watched()
    async with sessions() as session:
        await seed(session, setup)
        attempt = await add_attempt(session, setup, state=TriggerState.FAILED)
        attempt_id = attempt.id

    async def builder(symbol, evaluated_at):
        return snapshot(symbol, evaluated_at=evaluated_at)

    item = monitor(sessions, builder)
    await item.run_cycle()
    async with sessions() as session:
        reloaded = await session.get(TriggerAttemptRow, attempt_id)
        transition_count = await session.scalar(select(func.count(TriggerTransitionRow.id)))

    assert item.evaluation_count == item.persisted_count == 1
    assert item.terminal_count == 0
    assert reloaded.state == TriggerState.WAITING.value
    assert transition_count == 1


@pytest.mark.asyncio
async def test_valid_sequence_persists_new_confirmation_and_transition(sessions):
    setup = watched()
    async with sessions() as session:
        await seed(session, setup)
        attempt = await add_attempt(session, setup, state=TriggerState.FAILED)
        attempt_id = attempt.id
        context = resolve_trigger_context(watched=setup, watchlist=watchlist(), transitions=[])
    armed_ms = int(ARMED_AT.timestamp() * 1000)
    confirming_snapshot = LowerTimeframeTriggerSnapshot(
        symbol="BTCUSDT",
        evaluated_at=EVALUATED_AT,
        bars_5m=[
            bar(armed_ms, 5, low=99, close=101),
            LowerTimeframeBar(
                start_ms=armed_ms + 300_000,
                end_ms=armed_ms + 600_000,
                open=101,
                high=104,
                low=101,
                close=103,
                volume=10,
            ),
        ],
        bars_15m=[bar(armed_ms, 15, close=101)],
        data_status="PARTIAL",
    )

    async with sessions() as session:
        outcome = await evaluate_current_trigger_candidate(
            session,
            watched_setup_id=setup.id,
            expected_arm_key=context.arm_key,
            snapshot=confirming_snapshot,
        )
    async with sessions() as session:
        reloaded = await session.get(TriggerAttemptRow, attempt_id)
        transition = await session.scalar(select(TriggerTransitionRow))

    assert outcome.confirmed and outcome.persisted
    assert reloaded.state == TriggerState.CONFIRMED.value
    assert transition.from_state == TriggerState.FAILED.value
    assert transition.to_state == TriggerState.CONFIRMED.value


@pytest.mark.asyncio
async def test_fresh_revalidation_skips_disarmed_candidate(sessions):
    setup = watched()
    async with sessions() as session:
        await seed(session, setup)
        context = resolve_trigger_context(watched=setup, watchlist=watchlist(), transitions=[])
        expected_arm_key = context.arm_key
        setup.status = ScannerStatus.WATCH.value
        await session.commit()

    async with sessions() as session:
        outcome = await evaluate_current_trigger_candidate(
            session,
            watched_setup_id=setup.id,
            expected_arm_key=expected_arm_key,
            snapshot=snapshot(),
        )
        attempt_count = await session.scalar(select(func.count(TriggerAttemptRow.id)))

    assert outcome.failure == "NOT_ELIGIBLE"
    assert attempt_count == 0


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_without_mutating_prerequisites(
    sessions, monkeypatch
):
    setup = watched()
    original_state = dict(setup.state)
    async with sessions() as session:
        await seed(session, setup)
        context = resolve_trigger_context(watched=setup, watchlist=watchlist(), transitions=[])

    async def failed_persistence(session, *, context, result):
        session.add(
            TriggerAttemptRow(
                watched_setup_id=context.watched_setup_id,
                symbol=context.symbol,
                setup_type=context.setup_type.value,
                arm_key=context.arm_key,
                arm_source=context.arm_source.value,
                arm_transition_id=context.arm_transition_id,
                armed_at=context.armed_at,
                reference_level=Decimal(str(context.reference_level)),
                reference_source=context.reference_source.value,
                reference_metadata=dict(context.reference_metadata),
                retest_tolerance_bps=Decimal(str(context.retest_tolerance_bps)),
                failure_tolerance_bps=Decimal(str(context.failure_tolerance_bps)),
                state=result.state.value,
                result=result.model_dump(mode="json"),
                version=1,
                first_evaluated_at=result.evaluated_at,
                last_evaluated_at=result.evaluated_at,
            )
        )
        await session.flush()
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(runtime_module, "persist_trigger_result", failed_persistence)
    with pytest.raises(RuntimeError, match="injected persistence failure"):
        async with sessions() as session:
            await evaluate_current_trigger_candidate(
                session,
                watched_setup_id=setup.id,
                expected_arm_key=context.arm_key,
                snapshot=snapshot(),
            )

    async with sessions() as session:
        reloaded_setup = await session.get(WatchedSetupRow, setup.id)
        reloaded_watchlist = await session.scalar(select(ScannerWatchlistRow))
        attempt_count = await session.scalar(select(func.count(TriggerAttemptRow.id)))

    assert reloaded_setup.status == ScannerStatus.TRIGGER_ARMED.value
    assert reloaded_setup.state == original_state
    assert reloaded_setup.version == 1
    assert reloaded_watchlist.enabled is True
    assert reloaded_watchlist.enabled_playbooks == [
        "RANGE",
        "TREND_PULLBACK",
        "MACRO_BREAKOUT",
    ]
    assert attempt_count == 0


@pytest.mark.asyncio
async def test_fresh_revalidation_rejects_changed_arm(sessions):
    setup = watched()
    setup.version = 2
    async with sessions() as session:
        await seed(session, setup)
        arm_a = ScannerTransitionRow(
            id="arm-a",
            watched_setup_id=setup.id,
            symbol=setup.symbol,
            setup_type=setup.setup_type,
            from_status="WATCH",
            to_status="TRIGGER_ARMED",
            timestamp=ARMED_AT,
            state_before={},
            state_after=setup.state,
            version=2,
        )
        session.add(arm_a)
        await session.commit()
        setup.version = 4
        session.add(
            ScannerTransitionRow(
                id="arm-b",
                watched_setup_id=setup.id,
                symbol=setup.symbol,
                setup_type=setup.setup_type,
                from_status="WATCH",
                to_status="TRIGGER_ARMED",
                timestamp=ARMED_AT + timedelta(hours=1),
                state_before={},
                state_after=setup.state,
                version=4,
            )
        )
        await session.commit()

    async with sessions() as session:
        outcome = await evaluate_current_trigger_candidate(
            session,
            watched_setup_id=setup.id,
            expected_arm_key="TRANSITION:arm-a",
            snapshot=snapshot(),
        )
        attempt_count = await session.scalar(select(func.count(TriggerAttemptRow.id)))

    assert outcome.failure == "ARM_CHANGED_DURING_CYCLE"
    assert attempt_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "removed"])
async def test_fresh_revalidation_skips_watchlist_change(sessions, change):
    setup = watched()
    async with sessions() as session:
        await seed(session, setup)
        context = resolve_trigger_context(watched=setup, watchlist=watchlist(), transitions=[])
        current = await session.scalar(select(ScannerWatchlistRow))
        if change == "disabled":
            current.enabled = False
        else:
            current.enabled_playbooks = ["TREND_PULLBACK"]
        await session.commit()

    async with sessions() as session:
        outcome = await evaluate_current_trigger_candidate(
            session,
            watched_setup_id=setup.id,
            expected_arm_key=context.arm_key,
            snapshot=snapshot(),
        )
        attempt_count = await session.scalar(select(func.count(TriggerAttemptRow.id)))

    assert outcome.failure in {"WATCHLIST_DISABLED", "SETUP_DISABLED"}
    assert attempt_count == 0


def context_for_coverage():
    return resolve_trigger_context(
        watched=watched(), watchlist=watchlist(), transitions=[]
    )


def test_snapshot_history_coverage_boundaries():
    context = context_for_coverage()
    armed_ms = int(ARMED_AT.timestamp() * 1000)

    assert snapshot_covers_context(snapshot(), context)
    assert not snapshot_covers_context(snapshot(start_5m=armed_ms + 300_000), context)
    assert not snapshot_covers_context(snapshot(start_15m=armed_ms + 900_000), context)


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", ["5m", "15m"])
async def test_incomplete_history_skips_evaluation_and_persistence(sessions, truncated):
    setup = watched()
    async with sessions() as session:
        await seed(session, setup)
        context = resolve_trigger_context(watched=setup, watchlist=watchlist(), transitions=[])
    armed_ms = int(ARMED_AT.timestamp() * 1000)
    incomplete = snapshot(
        start_5m=armed_ms + (300_000 if truncated == "5m" else 0),
        start_15m=armed_ms + (900_000 if truncated == "15m" else 0),
    )

    async with sessions() as session:
        outcome = await evaluate_current_trigger_candidate(
            session,
            watched_setup_id=setup.id,
            expected_arm_key=context.arm_key,
            snapshot=incomplete,
        )
        attempt_count = await session.scalar(select(func.count(TriggerAttemptRow.id)))

    assert outcome.failure == "TRIGGER_HISTORY_INCOMPLETE"
    assert not outcome.evaluated
    assert attempt_count == 0


@pytest.mark.asyncio
async def test_recent_arm_with_current_partial_5m_is_evaluated(sessions):
    recent = EVALUATED_AT - timedelta(minutes=2)
    setup = watched()
    setup.created_at = recent
    async with sessions() as session:
        await seed(session, setup)
        context = resolve_trigger_context(watched=setup, watchlist=watchlist(), transitions=[])
    aligned_5m = ((int(recent.timestamp() * 1000) + 299_999) // 300_000) * 300_000
    aligned_15m = (int(recent.timestamp() * 1000) // 900_000) * 900_000
    recent_snapshot = snapshot(
        start_5m=aligned_5m,
        start_15m=aligned_15m,
        evaluated_at=EVALUATED_AT,
    )

    async with sessions() as session:
        outcome = await evaluate_current_trigger_candidate(
            session,
            watched_setup_id=setup.id,
            expected_arm_key=context.arm_key,
            snapshot=recent_snapshot,
        )

    assert outcome.evaluated and outcome.persisted
    async with sessions() as session:
        persisted = await session.scalar(select(TriggerAttemptRow))
    assert persisted.state == TriggerState.INDETERMINATE.value


@pytest.mark.asyncio
async def test_symbol_snapshot_failure_is_isolated(sessions, monkeypatch):
    async with sessions() as session:
        session.add_all(
            [watchlist("BTCUSDT"), watchlist("ETHUSDT"), watched(), watched(symbol="ETHUSDT")]
        )
        await session.commit()

    async def builder(symbol, evaluated_at):
        if symbol == "BTCUSDT":
            raise RuntimeError("offline")
        return snapshot(symbol, evaluated_at=evaluated_at)

    async def fake_runtime(session, **kwargs):
        return TriggerCandidateOutcome(evaluated=True, persisted=True)

    monkeypatch.setattr(monitor_module, "evaluate_current_trigger_candidate", fake_runtime)
    item = monitor(sessions, builder)
    await item.run_cycle()

    assert item.evaluated_symbols == ["ETHUSDT"]
    assert item.failed_symbols == ["BTCUSDT"]
    assert "BTCUSDT: snapshot fetch failed: offline" in item.last_error


@pytest.mark.asyncio
async def test_candidate_failure_and_success_put_symbol_in_both_lists(sessions, monkeypatch):
    outcomes = iter(
        [
            TriggerCandidateOutcome(failure="TRIGGER_HISTORY_INCOMPLETE"),
            TriggerCandidateOutcome(evaluated=True, persisted=True),
        ]
    )

    async def fake_runtime(session, **kwargs):
        return next(outcomes)

    monkeypatch.setattr(monitor_module, "evaluate_current_trigger_candidate", fake_runtime)
    async with sessions() as session:
        await seed(
            session,
            watched(ScannerSetupType.RANGE_LONG),
            watched(ScannerSetupType.TREND_PULLBACK_LONG),
        )
    item = monitor(sessions, lambda symbol, evaluated_at: asyncio.sleep(0, result=snapshot()))
    await item.run_cycle()

    assert item.evaluated_symbols == ["BTCUSDT"]
    assert item.failed_symbols == ["BTCUSDT"]
    assert item.persisted_count == 1
    assert item.last_success_at == item.last_cycle_started_at


def test_trigger_monitor_config_defaults_and_validation():
    defaults = Settings(_env_file=None)
    assert defaults.trigger_monitor_enabled is False
    assert defaults.trigger_monitor_interval_seconds == 15
    assert defaults.trigger_monitor_concurrency == 4
    with pytest.raises(ValidationError):
        Settings(trigger_monitor_interval_seconds=0, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(trigger_monitor_concurrency=0, _env_file=None)
    with pytest.raises(ValidationError, match="requires SETUP_SCANNER_ENABLED"):
        Settings(trigger_monitor_enabled=True, setup_scanner_enabled=False, _env_file=None)
    assert Settings(
        trigger_monitor_enabled=True, setup_scanner_enabled=True, _env_file=None
    )


@pytest.mark.asyncio
async def test_disabled_run_and_enabled_cancellation_lifecycle(sessions):
    disabled = monitor(sessions, None, enabled=False)
    await disabled.run()
    assert not disabled.running
    enabled = monitor(sessions, None)
    task = asyncio.create_task(enabled.run())
    for _ in range(20):
        if enabled.running and enabled.last_cycle_completed_at is not None:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not enabled.running


@pytest.mark.asyncio
async def test_lifespan_starts_and_cancels_one_trigger_monitor(monkeypatch):
    instances = []

    class FakeTriggerMonitor:
        def __init__(self, **kwargs):
            self.started = asyncio.Event()
            self.cancelled = False
            instances.append(self)

        async def run(self):
            self.started.set()
            try:
                await asyncio.Future()
            finally:
                self.cancelled = True

    monkeypatch.setattr(main_module, "TriggerMonitor", FakeTriggerMonitor)
    monkeypatch.setattr(main_module.settings, "trigger_monitor_enabled", True)
    monkeypatch.setattr(main_module.settings, "setup_scanner_enabled", False)
    monkeypatch.setattr(main_module.settings, "live_stream_enabled", False)
    monkeypatch.setattr(main_module.settings, "state_change_monitor_enabled", False)
    async with main_module.lifespan(main_module.app):
        await asyncio.wait_for(instances[0].started.wait(), timeout=1)
        assert len(instances) == 1
    assert instances[0].cancelled
    assert main_module.trigger_monitor_task is None
