from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.domain.models import Regime
from trading_copilot.domain.scanner import ScannerDataStatus, ScannerSetupType, ScannerStatus
from trading_copilot.persistence.models import (
    Base,
    ChartStructureRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TradePlanRow,
    WatchedSetupRow,
)
from trading_copilot.services import scanner_composer
from trading_copilot.services.scanner_composer import enabled_setup_types, evaluate_symbol
from trading_copilot.services.scanner_snapshot import (
    ScannerSymbolSnapshot,
    ScannerTimeframeSnapshot,
)

NOW = datetime(2026, 1, 1, 11, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'composer.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        await engine.dispose()


def snapshot(price=110, closes=(99,), evaluated_at=NOW):
    one_hour = ScannerTimeframeSnapshot(
        regime=Regime.UPTREND,
        close=closes[-1],
        ema12=105,
        ema21=100,
        stoch_k=50,
        stoch_d=50,
        prev_stoch_k=50,
        prev_stoch_d=50,
    )
    four_hour = ScannerTimeframeSnapshot(
        regime=Regime.UPTREND,
        close=price,
        ema12=105,
        ema21=100,
    )
    return ScannerSymbolSnapshot(
        symbol="BTCUSDT",
        current_price=price,
        evaluated_at=evaluated_at,
        one_hour=one_hour,
        four_hour=four_hour,
        completed_1h_closes=tuple(closes),
        reaction_state=None,
        data_status=ScannerDataStatus.PARTIAL,
    )


def watchlist(*playbooks):
    return ScannerWatchlistRow(
        id="watchlist-1",
        symbol="BTCUSDT",
        enabled=True,
        enabled_playbooks=list(playbooks),
        approach_tolerance_bps=Decimal(100),
        retest_tolerance_bps=Decimal(50),
        acceptance_bars=2,
    )


def plan(plan_id, side, setup_type):
    return TradePlanRow(
        id=plan_id,
        symbol="BTCUSDT",
        side=side,
        setup_type=setup_type,
        thesis="test",
        lifecycle_status="ACTIVE",
        hard_invalidation=Decimal(90),
        max_risk_percent=Decimal("0.01"),
    )


async def seed_all_structures(session):
    plans = [
        plan("trend-long", "LONG", "TREND_PULLBACK_LONG"),
        plan("trend-short", "SHORT", "TREND_PULLBACK_SHORT"),
        plan("range-long", "LONG", "RANGE_LONG"),
        plan("range-short", "SHORT", "RANGE_SHORT"),
    ]
    session.add_all(
        [
            *plans,
            FibDefinitionRow(
                trade_plan_id="trend-long",
                symbol="BTCUSDT",
                direction="LONG",
                swing_low=Decimal(100),
                swing_high=Decimal(200),
            ),
            FibDefinitionRow(
                trade_plan_id="trend-short",
                symbol="BTCUSDT",
                direction="SHORT",
                swing_low=Decimal(100),
                swing_high=Decimal(200),
            ),
            RangeDefinitionRow(
                trade_plan_id="range-long",
                symbol="BTCUSDT",
                range_low=Decimal(100),
                range_high=Decimal(120),
            ),
            RangeDefinitionRow(
                trade_plan_id="range-short",
                symbol="BTCUSDT",
                range_low=Decimal(100),
                range_high=Decimal(120),
            ),
            ChartStructureRow(
                id="macro-zone",
                symbol="BTCUSDT",
                timeframe="1D",
                structure_type="HORIZONTAL_ZONE",
                label="macro",
                lower_price=Decimal(95),
                upper_price=Decimal(105),
                active=True,
            ),
        ]
    )
    await session.commit()


def test_enabled_families_map_to_both_directions_without_enabling_others():
    assert enabled_setup_types(["TREND_PULLBACK"]) == [
        ScannerSetupType.TREND_PULLBACK_LONG,
        ScannerSetupType.TREND_PULLBACK_SHORT,
    ]
    assert enabled_setup_types(["RANGE_LONG"]) == [ScannerSetupType.RANGE_LONG]
    assert ScannerSetupType.MACRO_BREAKOUT_LONG not in enabled_setup_types(["RANGE"])


@pytest.mark.asyncio
async def test_one_shared_snapshot_evaluates_all_enabled_families_and_directions(sessions):
    async with sessions() as session:
        await seed_all_structures(session)
        outcome = await evaluate_symbol(
            session,
            watchlist("TREND_PULLBACK", "RANGE", "MACRO_BREAKOUT"),
            snapshot(closes=(101, 102)),
        )
        persisted = list(await session.scalars(select(WatchedSetupRow)))
        transition_count = await session.scalar(select(func.count(ScannerTransitionRow.id)))

    assert {result.setup_type for result in outcome.results} == set(ScannerSetupType)
    assert outcome.failed_setups == {}
    assert len(persisted) == 6
    assert all(row.version == 1 for row in persisted)
    assert transition_count == 0


@pytest.mark.asyncio
async def test_existing_trade_plan_link_reaches_structural_resolver(sessions):
    async with sessions() as session:
        await seed_all_structures(session)
        session.add(
            WatchedSetupRow(
                symbol="BTCUSDT",
                setup_type="TREND_PULLBACK_LONG",
                status="WATCH",
                state={},
                trade_plan_id="trend-long",
            )
        )
        await session.commit()

        outcome = await evaluate_symbol(
            session, watchlist("TREND_PULLBACK_LONG"), snapshot()
        )

    assert outcome.results[0].linked_trade_plan_id == "trend-long"


@pytest.mark.asyncio
async def test_existing_macro_acceptance_state_reaches_macro_evaluator(sessions):
    accepted = {
        "accepted_at": NOW.isoformat(),
        "accepted_breakout_level": 105.0,
        "accepted_structure_id": "macro-zone",
        "accepted_close_count": 2,
        "acceptance_bars": 2,
        "accepted_side": "LONG",
    }
    async with sessions() as session:
        await seed_all_structures(session)
        session.add(
            WatchedSetupRow(
                symbol="BTCUSDT",
                setup_type="MACRO_BREAKOUT_LONG",
                status="BREAKOUT_ACCEPTED",
                state=accepted,
            )
        )
        await session.commit()

        outcome = await evaluate_symbol(
            session, watchlist("MACRO_BREAKOUT_LONG"), snapshot(price=120)
        )
        persisted = await session.scalar(
            select(WatchedSetupRow).where(
                WatchedSetupRow.setup_type == "MACRO_BREAKOUT_LONG"
            )
        )

    assert outcome.results[0].status == ScannerStatus.RETEST_PENDING
    assert {
        key: persisted.state[key]
        for key in (
            "accepted_at",
            "accepted_breakout_level",
            "accepted_structure_id",
            "accepted_close_count",
            "acceptance_bars",
            "accepted_side",
        )
    } == accepted


@pytest.mark.asyncio
async def test_macro_persisted_state_contains_canonical_decision_evidence(sessions):
    async with sessions() as session:
        session.add(
            ChartStructureRow(
                id="macro-zone",
                symbol="BTCUSDT",
                timeframe="1D",
                structure_type="HORIZONTAL_ZONE",
                label="daily resistance",
                lower_price=Decimal(95),
                upper_price=Decimal(100),
                active=True,
            )
        )
        await session.commit()
        await evaluate_symbol(
            session,
            watchlist("MACRO_BREAKOUT_LONG"),
            snapshot(price=101, closes=(99,)),
        )
        persisted = await session.scalar(select(WatchedSetupRow))

    assert persisted.state == {
        "symbol": "BTCUSDT",
        "setup_type": "MACRO_BREAKOUT_LONG",
        "side": "LONG",
        "status": "BREAKOUT_ATTEMPT",
        "price": 101.0,
        "evaluated_at": NOW.isoformat(),
        "structure": {
            "structure_id": "macro-zone",
            "label": "daily resistance",
            "type": "HORIZONTAL_ZONE",
            "breakout_level": 100.0,
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "lower_price": 95.0,
            "upper_price": 100.0,
            "anchor_one_time": None,
            "anchor_one_price": None,
            "anchor_two_time": None,
            "anchor_two_price": None,
            "approach_zone": {"lower": 99.0, "upper": 101.0},
            "retest_zone": {"lower": 99.5, "upper": 100.5},
        },
        "distance_bps": 100.0,
        "qualifying_close_count": 0,
        "required_acceptance_bars": 2,
        "blocking_reasons": [],
        "next_conditions": ["await completed 1H close beyond structure"],
        "data_status": "PARTIAL",
    }


@pytest.mark.asyncio
async def test_macro_transition_preserves_canonical_before_and_after_snapshots(sessions):
    async with sessions() as session:
        session.add(
            ChartStructureRow(
                id="macro-zone",
                symbol="BTCUSDT",
                timeframe="1D",
                structure_type="HORIZONTAL_ZONE",
                label="daily resistance",
                lower_price=Decimal(95),
                upper_price=Decimal(100),
                active=True,
            )
        )
        await session.commit()
        item = watchlist("MACRO_BREAKOUT_LONG")
        await evaluate_symbol(session, item, snapshot(price=101, closes=(99,)))
        current = await session.scalar(select(WatchedSetupRow))
        prior_payload = dict(current.state)

        await evaluate_symbol(session, item, snapshot(price=101, closes=(99, 101)))
        transition = await session.scalar(select(ScannerTransitionRow))

    assert transition.from_status == "BREAKOUT_ATTEMPT"
    assert transition.to_status == "ACCEPTANCE_PENDING"
    assert transition.state_before == prior_payload
    assert transition.state_before["status"] == "BREAKOUT_ATTEMPT"
    assert transition.state_after["status"] == "ACCEPTANCE_PENDING"
    assert transition.state_after["symbol"] == "BTCUSDT"
    assert transition.state_after["setup_type"] == "MACRO_BREAKOUT_LONG"
    assert transition.state_after["structure"] == {
        "structure_id": "macro-zone",
        "label": "daily resistance",
        "type": "HORIZONTAL_ZONE",
        "breakout_level": 100.0,
        "symbol": "BTCUSDT",
        "timeframe": "1D",
        "lower_price": 95.0,
        "upper_price": 100.0,
        "anchor_one_time": None,
        "anchor_one_price": None,
        "anchor_two_time": None,
        "anchor_two_price": None,
        "approach_zone": {"lower": 99.0, "upper": 101.0},
        "retest_zone": {"lower": 99.5, "upper": 100.5},
    }
    assert transition.state_after["distance_bps"] == pytest.approx(100.0)
    assert transition.state_after["qualifying_close_count"] == 1
    assert transition.state_after["required_acceptance_bars"] == 2
    assert transition.state_after["blocking_reasons"] == []
    assert transition.state_after["next_conditions"] == [
        "await additional completed 1H acceptance"
    ]
    assert transition.state_after["data_status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_unchanged_status_is_quiet_and_progression_creates_one_transition(sessions):
    async with sessions() as session:
        session.add(
            ChartStructureRow(
                id="macro-zone",
                symbol="BTCUSDT",
                timeframe="1D",
                structure_type="HORIZONTAL_ZONE",
                lower_price=Decimal(95),
                upper_price=Decimal(100),
                active=True,
            )
        )
        await session.commit()
        item = watchlist("MACRO_BREAKOUT_LONG")
        await evaluate_symbol(session, item, snapshot(price=90))
        await evaluate_symbol(session, item, snapshot(price=90))
        quiet_count = await session.scalar(select(func.count(ScannerTransitionRow.id)))
        await evaluate_symbol(session, item, snapshot(price=99.5))
        current = await session.scalar(select(WatchedSetupRow))
        transitions = list(await session.scalars(select(ScannerTransitionRow)))

    assert quiet_count == 0
    assert current.version == 2
    assert current.status == "APPROACHING_BREAKOUT"
    assert len(transitions) == 1


@pytest.mark.asyncio
async def test_failed_sibling_setup_does_not_fabricate_state_or_block_success(
    sessions, monkeypatch
):
    async with sessions() as session:
        await seed_all_structures(session)
        real_evaluator = scanner_composer.evaluate_scanner_playbook

        def fail_short(**kwargs):
            if kwargs["watched"].setup_type.endswith("SHORT"):
                raise RuntimeError("isolated setup failure")
            return real_evaluator(**kwargs)

        monkeypatch.setattr(scanner_composer, "evaluate_scanner_playbook", fail_short)
        outcome = await evaluate_symbol(
            session, watchlist("TREND_PULLBACK", "RANGE_LONG"), snapshot()
        )
        rows = list(await session.scalars(select(WatchedSetupRow)))

    assert [result.setup_type for result in outcome.results] == [
        ScannerSetupType.TREND_PULLBACK_LONG,
        ScannerSetupType.RANGE_LONG,
    ]
    assert outcome.failed_setups == {"TREND_PULLBACK_SHORT": "isolated setup failure"}
    assert {row.setup_type for row in rows} == {"TREND_PULLBACK_LONG", "RANGE_LONG"}


def accepted_state(side, structure_id="accepted-line", level=100.0):
    return {
        "accepted_at": NOW.isoformat(),
        "accepted_breakout_level": level,
        "accepted_structure_id": structure_id,
        "accepted_close_count": 2,
        "acceptance_bars": 2,
        "accepted_side": side,
    }


def accepted_trendline(*, active=True, malformed=False):
    start_ms = int(NOW.timestamp() * 1000)
    return ChartStructureRow(
        id="accepted-line",
        symbol="BTCUSDT",
        timeframe="1D",
        structure_type="TRENDLINE",
        label="accepted trendline",
        anchor_one_time=start_ms,
        anchor_one_price=Decimal(100),
        anchor_two_time=None if malformed else start_ms + 3_600_000,
        anchor_two_price=None if malformed else Decimal(105),
        active=active,
    )


@pytest.mark.parametrize(
    ("setup_type", "side"),
    [
        ("MACRO_BREAKOUT_LONG", "LONG"),
        ("MACRO_BREAKOUT_SHORT", "SHORT"),
    ],
)
@pytest.mark.asyncio
async def test_accepted_trendline_keeps_original_reference_level_for_retest(
    sessions, setup_type, side
):
    later = NOW + timedelta(hours=1)
    async with sessions() as session:
        session.add_all(
            [
                accepted_trendline(),
                WatchedSetupRow(
                    symbol="BTCUSDT",
                    setup_type=setup_type,
                    status="BREAKOUT_ACCEPTED",
                    state=accepted_state(side),
                ),
            ]
        )
        await session.commit()

        outcome = await evaluate_symbol(
            session,
            watchlist(setup_type),
            snapshot(price=100.4, evaluated_at=later),
        )
        persisted = await session.scalar(
            select(WatchedSetupRow).where(WatchedSetupRow.setup_type == setup_type)
        )

    assert outcome.failed_setups == {}
    assert outcome.results[0].status == ScannerStatus.TRIGGER_ARMED
    assert outcome.results[0].structure["breakout_level"] == 100.0
    assert persisted.state["accepted_breakout_level"] == 100.0
    assert persisted.state["distance_bps"] == pytest.approx(40.0)


@pytest.mark.parametrize(
    "status", ["BREAKOUT_ACCEPTED", "RETEST_PENDING", "TRIGGER_ARMED"]
)
@pytest.mark.parametrize("structure_state", ["deleted", "inactive", "malformed"])
@pytest.mark.asyncio
async def test_stale_accepted_structure_reference_self_heals(
    sessions, structure_state, status
):
    prior = accepted_state("LONG")
    async with sessions() as session:
        current = WatchedSetupRow(
            symbol="BTCUSDT",
            setup_type="MACRO_BREAKOUT_LONG",
            status=status,
            state=prior,
        )
        session.add(current)
        if structure_state == "inactive":
            session.add(accepted_trendline(active=False))
        elif structure_state == "malformed":
            session.add(accepted_trendline(malformed=True))
        await session.commit()
        watched_id = current.id
        outcome = await evaluate_symbol(
            session,
            watchlist("MACRO_BREAKOUT_LONG"),
            snapshot(price=100.4, evaluated_at=NOW + timedelta(hours=1)),
        )

    async with sessions() as fresh_session:
        reloaded = await fresh_session.get(WatchedSetupRow, watched_id)
        transition_count = await fresh_session.scalar(
            select(func.count(ScannerTransitionRow.id))
        )

    assert outcome.failed_setups == {}
    assert outcome.results[0].status == ScannerStatus.WATCH
    assert outcome.results[0].blocking_reasons == ["STRUCTURE_REQUIRED"]
    assert reloaded.status == "WATCH"
    assert reloaded.state["reconciliation_reason"] == "STALE_STRUCTURE_REFERENCE_RECONCILED"
    assert reloaded.state["lifecycle_reset_reason"] == (
        "STALE_STRUCTURE_REFERENCE_RECONCILED"
    )
    assert reloaded.state["lifecycle_episode"] == 2
    assert not any(key.startswith("accepted_") for key in reloaded.state)
    assert reloaded.state["structure"]["structure_id"] is None
    assert reloaded.state["structure"]["breakout_level"] is None
    assert reloaded.version == 2
    assert transition_count == 1


@pytest.mark.asyncio
async def test_pre_acceptance_missing_structure_still_persists_watch_blocker(sessions):
    async with sessions() as session:
        outcome = await evaluate_symbol(
            session,
            watchlist("MACRO_BREAKOUT_LONG"),
            snapshot(),
        )
        persisted = await session.scalar(select(WatchedSetupRow))

    assert outcome.failed_setups == {}
    assert outcome.results[0].status == ScannerStatus.WATCH
    assert outcome.results[0].blocking_reasons == ["STRUCTURE_REQUIRED"]
    assert persisted.status == "WATCH"
    assert persisted.state["blocking_reasons"] == ["STRUCTURE_REQUIRED"]
