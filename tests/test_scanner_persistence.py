from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.domain.scanner import ScannerStatus
from trading_copilot.persistence.models import (
    Base,
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TradePlanRow,
    WatchedSetupRow,
)
from trading_copilot.services.scanner_macro import evaluate_macro_breakout
from trading_copilot.services.scanner_persistence import persist_candidate

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scanner.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, sessions
    finally:
        await engine.dispose()


def trade_plan(plan_id="plan-1"):
    return TradePlanRow(
        id=plan_id,
        symbol="BTCUSDT",
        side="LONG",
        setup_type="TREND_PULLBACK_LONG",
        thesis="test",
        hard_invalidation=Decimal(90),
        max_risk_percent=Decimal("0.01"),
    )


@pytest.mark.asyncio
async def test_first_observation_starts_at_version_one_without_transition(database):
    _, sessions = database
    async with sessions() as session:
        row = await persist_candidate(
            session,
            symbol="btcusdt",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state={"price": 100},
        )
        transition_count = await session.scalar(select(func.count(ScannerTransitionRow.id)))

    assert row.symbol == "BTCUSDT"
    assert row.status == "WATCH"
    assert row.state == {"price": 100}
    assert row.version == 1
    assert row.last_evaluated_at is not None
    assert transition_count == 0


@pytest.mark.asyncio
async def test_same_status_updates_payload_without_transition_or_version_change(database):
    _, sessions = database
    async with sessions() as session:
        original = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state={"price": 100, "distance": 50},
        )
        first_evaluated_at = original.last_evaluated_at
        updated = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state={"price": 101, "distance": 20, "reaction": "changed"},
        )
        transition_count = await session.scalar(select(func.count(ScannerTransitionRow.id)))

    assert updated.state == {"price": 101, "distance": 20, "reaction": "changed"}
    assert updated.version == 1
    assert updated.last_evaluated_at >= first_evaluated_at
    assert transition_count == 0


@pytest.mark.asyncio
async def test_changed_status_writes_true_snapshots_and_increments_once(database):
    _, sessions = database
    before = {"price": 100, "distance": 50}
    after = {"price": 101, "distance": 10}
    async with sessions() as session:
        current = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state=before,
        )
        current = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="APPROACHING_LOCATION",
            state=after,
        )
        transitions = list(
            await session.scalars(
                select(ScannerTransitionRow).order_by(ScannerTransitionRow.version)
            )
        )

    assert current.status == "APPROACHING_LOCATION"
    assert current.version == 2
    assert len(transitions) == 1
    assert transitions[0].from_status == "WATCH"
    assert transitions[0].to_status == "APPROACHING_LOCATION"
    assert transitions[0].state_before == before
    assert transitions[0].state_after == after
    assert transitions[0].version == current.version


@pytest.mark.asyncio
async def test_lifecycle_episode_starts_at_reset_version_and_is_carried_forward(database):
    _, sessions = database
    async with sessions() as session:
        await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="MACRO_BREAKOUT_LONG",
            status="TRIGGER_ARMED",
            state={"accepted_structure_id": "old"},
        )
        reset = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="MACRO_BREAKOUT_LONG",
            status="WATCH",
            state={
                "blocking_reasons": ["STRUCTURE_REQUIRED"],
                "lifecycle_reset_reason": "STALE_STRUCTURE_REFERENCE_RECONCILED",
            },
        )
        reset_version = reset.version
        current = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="MACRO_BREAKOUT_LONG",
            status="BREAKOUT_ATTEMPT",
            state={"structure": {"structure_id": "replacement"}},
        )
        transitions = list(
            await session.scalars(
                select(ScannerTransitionRow).order_by(ScannerTransitionRow.version)
            )
        )

    assert reset_version == 2
    assert transitions[0].state_after["lifecycle_episode"] == 2
    assert transitions[0].state_after["lifecycle_reset_reason"] == (
        "STALE_STRUCTURE_REFERENCE_RECONCILED"
    )
    assert current.version == 3
    assert current.state["lifecycle_episode"] == 2
    assert "lifecycle_reset_reason" not in current.state
    assert transitions[1].state_after["lifecycle_episode"] == 2


@pytest.mark.asyncio
async def test_second_transition_creates_next_version(database):
    _, sessions = database
    async with sessions() as session:
        for status in ("WATCH", "APPROACHING_LOCATION", "AT_LOCATION"):
            current = await persist_candidate(
                session,
                symbol="BTCUSDT",
                setup_type="TREND_PULLBACK_LONG",
                status=status,
                state={"status": status},
            )
        versions = list(
            await session.scalars(
                select(ScannerTransitionRow.version).order_by(ScannerTransitionRow.version)
            )
        )

    assert current.version == 3
    assert versions == [2, 3]


@pytest.mark.asyncio
async def test_duplicate_transition_version_is_rejected(database):
    _, sessions = database
    async with sessions() as session:
        current = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state={},
        )
        duplicate_values = {
            "watched_setup_id": current.id,
            "symbol": current.symbol,
            "setup_type": current.setup_type,
            "from_status": "WATCH",
            "to_status": "APPROACHING_LOCATION",
            "timestamp": NOW,
            "state_before": {},
            "state_after": {},
            "version": 2,
        }
        session.add_all(
            [ScannerTransitionRow(**duplicate_values), ScannerTransitionRow(**duplicate_values)]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_transition_failure_rolls_back_current_row_atomically(database):
    engine, sessions = database
    async with sessions() as session:
        original = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state={"committed": True},
        )
        watched_id = original.id

    def fail_transition_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO SCANNER_TRANSITIONS"):
            raise RuntimeError("injected transition failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_transition_insert)
    try:
        async with sessions() as session:
            with pytest.raises(RuntimeError, match="injected transition failure"):
                await persist_candidate(
                    session,
                    symbol="BTCUSDT",
                    setup_type="TREND_PULLBACK_LONG",
                    status="APPROACHING_LOCATION",
                    state={"committed": False},
                )
            await session.rollback()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_transition_insert)

    async with sessions() as session:
        reloaded = await session.get(WatchedSetupRow, watched_id)
        transition_count = await session.scalar(select(func.count(ScannerTransitionRow.id)))

    assert reloaded.status == "WATCH"
    assert reloaded.state == {"committed": True}
    assert reloaded.version == 1
    assert transition_count == 0


@pytest.mark.asyncio
async def test_fresh_session_reload_preserves_macro_acceptance_and_plan_link(database):
    _, sessions = database
    macro_state = {
        "accepted_at": NOW.isoformat(),
        "accepted_breakout_level": 100,
        "accepted_structure_id": "structure-a",
        "accepted_close_count": 2,
        "acceptance_bars": 2,
        "accepted_side": "LONG",
    }
    async with sessions() as session:
        session.add(trade_plan())
        await session.commit()
        persisted = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="MACRO_BREAKOUT_LONG",
            status="BREAKOUT_ACCEPTED",
            state=macro_state,
            trade_plan_id="plan-1",
        )
        watched_id = persisted.id

    async with sessions() as fresh_session:
        reloaded = await fresh_session.get(WatchedSetupRow, watched_id)
        restarted = evaluate_macro_breakout(
            side="LONG",
            current_price=110,
            breakout_level=100,
            approach_tolerance_bps=50,
            retest_tolerance_bps=25,
            acceptance_bars=2,
            completed_1h_closes=[],
            previous_status=reloaded.status,
            previous_state=reloaded.state,
            structure_id="structure-a",
            structure_metadata={},
            evaluated_at=NOW,
        )

    assert reloaded.status == "BREAKOUT_ACCEPTED"
    assert reloaded.state == macro_state
    assert reloaded.version == 1
    assert reloaded.trade_plan_id == "plan-1"
    assert restarted.status == ScannerStatus.RETEST_PENDING


@pytest.mark.asyncio
async def test_trade_plan_link_survives_updates_and_transitions(database):
    _, sessions = database
    async with sessions() as session:
        session.add(trade_plan())
        await session.commit()
        await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state={},
            trade_plan_id="plan-1",
        )
        unchanged = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="WATCH",
            state={"price": 100},
        )
        changed = await persist_candidate(
            session,
            symbol="BTCUSDT",
            setup_type="TREND_PULLBACK_LONG",
            status="AT_LOCATION",
            state={"price": 101},
        )

    assert unchanged.trade_plan_id == "plan-1"
    assert changed.trade_plan_id == "plan-1"


@pytest.mark.asyncio
async def test_watched_setup_symbol_and_setup_type_are_unique_together(database):
    _, sessions = database
    async with sessions() as session:
        session.add_all(
            [
                WatchedSetupRow(
                    symbol="BTCUSDT", setup_type="TREND_PULLBACK_LONG", status="WATCH", state={}
                ),
                WatchedSetupRow(
                    symbol="BTCUSDT", setup_type="RANGE_LONG", status="WATCH", state={}
                ),
            ]
        )
        await session.commit()
        session.add(
            WatchedSetupRow(
                symbol="btcusdt", setup_type="TREND_PULLBACK_LONG", status="WATCH", state={}
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_watchlist_normalizes_symbol_and_persists_configuration(database):
    _, sessions = database
    async with sessions() as session:
        row = ScannerWatchlistRow(
            symbol="btcusdt",
            enabled=False,
            enabled_playbooks=["TREND_PULLBACK_LONG", "RANGE_LONG"],
            approach_tolerance_bps=Decimal("75.5"),
            retest_tolerance_bps=Decimal("12.5"),
            acceptance_bars=3,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    assert row.symbol == "BTCUSDT"
    assert row.enabled is False
    assert row.enabled_playbooks == ["TREND_PULLBACK_LONG", "RANGE_LONG"]
    assert row.approach_tolerance_bps == Decimal("75.5000")
    assert row.retest_tolerance_bps == Decimal("12.5000")
    assert row.acceptance_bars == 3


@pytest.mark.asyncio
async def test_watchlist_defaults_are_deterministic(database):
    _, sessions = database
    async with sessions() as session:
        row = ScannerWatchlistRow(symbol="ethusdt")
        session.add(row)
        await session.commit()
        await session.refresh(row)

    assert row.symbol == "ETHUSDT"
    assert row.enabled is True
    assert row.enabled_playbooks == []
    assert row.approach_tolerance_bps == Decimal("50.0000")
    assert row.retest_tolerance_bps == Decimal("25.0000")
    assert row.acceptance_bars == 2


@pytest.mark.asyncio
async def test_duplicate_watchlist_symbol_is_rejected(database):
    _, sessions = database
    async with sessions() as session:
        session.add_all(
            [ScannerWatchlistRow(symbol="BTCUSDT"), ScannerWatchlistRow(symbol="btcusdt")]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_watchlist_acceptance_bars_must_be_positive(database):
    _, sessions = database
    async with sessions() as session:
        session.add(ScannerWatchlistRow(symbol="BTCUSDT", acceptance_bars=0))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_transition_composite_indexes_exist(database):
    engine, _ = database

    def index_names(connection):
        inspector = inspect(connection)
        indexes = {item["name"] for item in inspector.get_indexes("scanner_transitions")}
        unique_constraints = {
            item["name"] for item in inspector.get_unique_constraints("scanner_transitions")
        }
        return indexes, unique_constraints

    async with engine.connect() as connection:
        indexes, unique_constraints = await connection.run_sync(index_names)

    assert "ix_scanner_transition_symbol_time" in indexes
    assert "ix_scanner_transition_type_time" in indexes
    assert "uq_scanner_transition_version" in unique_constraints
