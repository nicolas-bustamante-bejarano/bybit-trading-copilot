import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import trading_copilot.main as main_module
from trading_copilot.domain.models import Regime
from trading_copilot.domain.scanner import ScannerDataStatus, ScannerResult
from trading_copilot.persistence.models import (
    Base,
    ScannerTransitionRow,
    ScannerWatchlistRow,
    WatchedSetupRow,
)
from trading_copilot.services.scanner_composer import SymbolEvaluationOutcome
from trading_copilot.services.scanner_monitor import SetupScannerMonitor
from trading_copilot.services.scanner_snapshot import (
    ScannerSymbolSnapshot,
    ScannerTimeframeSnapshot,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'monitor.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        await engine.dispose()


def watchlist(symbol, *, enabled=True, row_id=None):
    return ScannerWatchlistRow(
        id=row_id or f"watch-{symbol}",
        symbol=symbol,
        enabled=enabled,
        enabled_playbooks=["TREND_PULLBACK"],
    )


def snapshot(symbol):
    timeframe = ScannerTimeframeSnapshot(
        regime=Regime.RANGE,
        close=100,
        ema12=100,
        ema21=100,
    )
    return ScannerSymbolSnapshot(
        symbol=symbol,
        current_price=100,
        evaluated_at=NOW,
        one_hour=timeframe,
        four_hour=timeframe,
        completed_1h_closes=(99,),
        reaction_state=None,
        data_status=ScannerDataStatus.PARTIAL,
    )


def result(symbol):
    return ScannerResult(
        symbol=symbol,
        setup_type="TREND_PULLBACK_LONG",
        side="LONG",
        status="WATCH",
        price=100,
        evaluated_at=NOW,
    )


def monitor(sessions, builder, composer, *, enabled=True, concurrency=4, interval=30):
    return SetupScannerMonitor(
        enabled=enabled,
        interval_seconds=interval,
        concurrency=concurrency,
        sessions=sessions,
        build_snapshot=builder,
        compose_symbol=composer,
    )


@pytest.mark.asyncio
async def test_disabled_monitor_run_does_not_start(sessions):
    async def unused_builder(symbol, evaluated_at):
        raise AssertionError("must not run")

    item = monitor(sessions, unused_builder, None, enabled=False)
    await item.run()

    assert item.running is False
    assert item.last_cycle_started_at is None


@pytest.mark.asyncio
async def test_empty_watchlist_completes_without_advancing_last_success(sessions):
    item = monitor(sessions, None, None)

    await item.run_cycle()

    status = item.status()
    assert status.watchlist_count == 0
    assert status.setup_count == 0
    assert status.evaluated_symbols == []
    assert status.failed_symbols == []
    assert status.last_cycle_completed_at is not None
    assert status.last_success_at is None
    assert status.last_cycle_duration_ms is not None


@pytest.mark.asyncio
async def test_cycle_skips_disabled_rows_and_isolates_failed_symbol(sessions):
    async with sessions() as session:
        session.add_all([watchlist("BTCUSDT"), watchlist("ETHUSDT"), watchlist("SOLUSDT", enabled=False)])
        await session.commit()

    calls = []

    async def builder(symbol, evaluated_at):
        calls.append(symbol)
        if symbol == "BTCUSDT":
            raise RuntimeError("market unavailable")
        return snapshot(symbol)

    async def composer(session, item, shared_snapshot):
        return SymbolEvaluationOutcome(results=[result(item.symbol)])

    item = monitor(sessions, builder, composer)
    await item.run_cycle()
    status = item.status()

    assert sorted(calls) == ["BTCUSDT", "ETHUSDT"]
    assert status.watchlist_count == 2
    assert status.setup_count == 1
    assert status.evaluated_symbols == ["ETHUSDT"]
    assert status.failed_symbols == ["BTCUSDT"]
    assert "BTCUSDT: market unavailable" in status.last_error
    assert status.last_success_at == status.last_cycle_started_at


@pytest.mark.asyncio
async def test_snapshot_failure_leaves_existing_candidate_and_transitions_untouched(sessions):
    async with sessions() as session:
        session.add_all(
            [
                watchlist("BTCUSDT"),
                WatchedSetupRow(
                    symbol="BTCUSDT",
                    setup_type="MACRO_BREAKOUT_LONG",
                    status="BREAKOUT_ACCEPTED",
                    state={"accepted_at": "preserved"},
                ),
            ]
        )
        await session.commit()

    async def failed_builder(symbol, evaluated_at):
        raise RuntimeError("bad snapshot")

    async def forbidden_composer(session, item, shared_snapshot):
        raise AssertionError("composer must not run")

    item = monitor(sessions, failed_builder, forbidden_composer)
    await item.run_cycle()

    async with sessions() as session:
        current = await session.scalar(select(WatchedSetupRow))
        transition_count = await session.scalar(select(func.count(ScannerTransitionRow.id)))

    assert current.status == "BREAKOUT_ACCEPTED"
    assert current.state == {"accepted_at": "preserved"}
    assert current.version == 1
    assert transition_count == 0


class FakeScalars:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalars(self, _query):
        return FakeScalars(self.rows)


class FakeSessions:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        return FakeSession(self.rows)


@pytest.mark.asyncio
async def test_duplicate_symbols_are_deduped_and_snapshot_built_once():
    rows = [
        watchlist("BTCUSDT", row_id="watch-1"),
        watchlist("BTCUSDT", row_id="watch-2"),
    ]
    calls = []

    async def builder(symbol, evaluated_at):
        calls.append(symbol)
        return snapshot(symbol)

    async def composer(session, item, shared_snapshot):
        return SymbolEvaluationOutcome(results=[result(item.symbol)] * 6)

    item = monitor(FakeSessions(rows), builder, composer)
    await item.run_cycle()

    assert calls == ["BTCUSDT"]
    assert item.watchlist_count == 1
    assert item.setup_count == 6


@pytest.mark.asyncio
async def test_concurrency_bound_is_respected_without_timing_dependency():
    rows = [watchlist(symbol) for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    active = 0
    maximum = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    async def builder(symbol, evaluated_at):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 2:
            two_started.set()
        await release.wait()
        active -= 1
        return snapshot(symbol)

    async def composer(session, item, shared_snapshot):
        return SymbolEvaluationOutcome(results=[])

    item = monitor(FakeSessions(rows), builder, composer, concurrency=2)
    cycle = asyncio.create_task(item.run_cycle())
    await asyncio.wait_for(two_started.wait(), timeout=1)
    assert maximum == 2
    release.set()
    await cycle
    assert maximum == 2


@pytest.mark.asyncio
async def test_run_cancellation_resets_running_flag(sessions):
    item = monitor(sessions, None, None, interval=60)
    task = asyncio.create_task(item.run())
    for _ in range(10):
        if item.running and item.last_cycle_completed_at is not None:
            break
        await asyncio.sleep(0)

    assert item.running is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert item.running is False


@pytest.mark.asyncio
async def test_lifespan_launches_and_cancels_exactly_one_enabled_scanner(monkeypatch):
    instances = []

    class FakeMonitor:
        def __init__(self, **kwargs):
            self.run_started = asyncio.Event()
            self.cancelled = False
            instances.append(self)

        async def run(self):
            self.run_started.set()
            try:
                await asyncio.Future()
            finally:
                self.cancelled = True

    monkeypatch.setattr(main_module, "SetupScannerMonitor", FakeMonitor)
    monkeypatch.setattr(main_module.settings, "setup_scanner_enabled", True)
    monkeypatch.setattr(main_module.settings, "live_stream_enabled", False)
    monkeypatch.setattr(main_module.settings, "state_change_monitor_enabled", False)
    main_module.setup_scanner_monitor_task = None

    async with main_module.lifespan(main_module.app):
        await asyncio.wait_for(instances[0].run_started.wait(), timeout=1)
        assert len(instances) == 1

    assert instances[0].cancelled is True


@pytest.mark.asyncio
async def test_lifespan_does_not_launch_disabled_scanner(monkeypatch):
    run_calls = 0

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            nonlocal run_calls
            run_calls += 1

    monkeypatch.setattr(main_module, "SetupScannerMonitor", FakeMonitor)
    monkeypatch.setattr(main_module.settings, "setup_scanner_enabled", False)
    monkeypatch.setattr(main_module.settings, "live_stream_enabled", False)
    monkeypatch.setattr(main_module.settings, "state_change_monitor_enabled", False)

    async with main_module.lifespan(main_module.app):
        await asyncio.sleep(0)

    assert run_calls == 0
    assert main_module.setup_scanner_monitor_task is None
