from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.domain.models import Regime
from trading_copilot.domain.scanner import ScannerDataStatus
from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    Base,
    ChartStructureRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TradePlanRow,
    TriggerAttemptRow,
    WatchedSetupRow,
)
from trading_copilot.services.scanner_composer import evaluate_symbol
from trading_copilot.services.scanner_monitor import SetupScannerMonitor
from trading_copilot.services.scanner_snapshot import (
    ScannerSymbolSnapshot,
    ScannerTimeframeSnapshot,
)

NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def api_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'invalidation.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


def accepted_state(structure_id: str) -> dict:
    return {
        "symbol": "BTCUSDT",
        "setup_type": "MACRO_BREAKOUT_LONG",
        "side": "LONG",
        "status": "TRIGGER_ARMED",
        "price": 110,
        "evaluated_at": NOW.isoformat(),
        "data_status": "CONFIRMED",
        "structure": {"structure_id": structure_id, "breakout_level": 100},
        "accepted_at": NOW.isoformat(),
        "accepted_breakout_level": 100,
        "accepted_structure_id": structure_id,
        "accepted_close_count": 2,
        "acceptance_bars": 2,
        "accepted_side": "LONG",
    }


def plan(plan_id: str, setup_type: str) -> TradePlanRow:
    return TradePlanRow(
        id=plan_id,
        symbol="BTCUSDT",
        side="LONG",
        setup_type=setup_type,
        thesis="test",
        lifecycle_status="ACTIVE",
        hard_invalidation=Decimal(90),
        max_risk_percent=Decimal("0.01"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["BREAKOUT_ACCEPTED", "RETEST_PENDING", "TRIGGER_ARMED"])
async def test_macro_delete_resets_accepted_lifecycle_with_audit_transition(
    api_client, status
):
    client, sessions = api_client
    async with sessions() as session:
        structure = ChartStructureRow(
            id="macro-1",
            symbol="BTCUSDT",
            timeframe="4h",
            structure_type="HORIZONTAL_ZONE",
            label="resistance",
            lower_price=Decimal(100),
            upper_price=Decimal(100),
            active=True,
        )
        watched = WatchedSetupRow(
            id="setup-1",
            symbol="BTCUSDT",
            setup_type="MACRO_BREAKOUT_LONG",
            status=status,
            state=accepted_state(structure.id) | {"status": status},
            version=4,
            last_evaluated_at=NOW,
        )
        session.add_all([structure, watched])
        if status == "TRIGGER_ARMED":
            session.add(
                TriggerAttemptRow(
                    id="attempt-1",
                    watched_setup_id=watched.id,
                    symbol="BTCUSDT",
                    setup_type=watched.setup_type,
                    arm_key="TRANSITION:old",
                    arm_source="ARM_TRANSITION",
                    arm_transition_id="old",
                    armed_at=NOW,
                    reference_level=Decimal(100),
                    reference_source="MACRO_BREAKOUT_LEVEL",
                    reference_metadata={"structure_id": structure.id},
                    retest_tolerance_bps=Decimal(25),
                    failure_tolerance_bps=Decimal(25),
                    state="WAITING",
                    result={"state": "WAITING"},
                    version=1,
                    first_evaluated_at=NOW,
                    last_evaluated_at=NOW,
                )
            )
        await session.commit()

    response = await client.delete("/chart-structures/macro-1")
    assert response.status_code == 204

    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, "setup-1")
        assert watched.status == "WATCH"
        assert watched.version == 5
        assert watched.state["structure"] == {}
        assert watched.state["blocking_reasons"][0] == "STRUCTURE_REQUIRED"
        assert not any(key.startswith("accepted_") for key in watched.state)
        transition = await session.scalar(
            select(ScannerTransitionRow).where(
                ScannerTransitionRow.watched_setup_id == watched.id
            )
        )
        assert transition.from_status == status
        assert transition.to_status == "WATCH"
        assert transition.state_before["accepted_structure_id"] == "macro-1"
        assert transition.state_after == watched.state
        if status == "TRIGGER_ARMED":
            assert await session.get(TriggerAttemptRow, "attempt-1") is not None
    assert (await client.get("/trigger/current?watched_setup_id=setup-1")).json() == []


@pytest.mark.asyncio
async def test_macro_geometry_edit_resets_old_acceptance_before_fresh_evaluation(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all(
            [
                ChartStructureRow(
                    id="macro-edit",
                    symbol="BTCUSDT",
                    timeframe="4h",
                    structure_type="HORIZONTAL_ZONE",
                    label="resistance",
                    lower_price=Decimal(100),
                    upper_price=Decimal(100),
                    active=True,
                ),
                WatchedSetupRow(
                    id="setup-edit",
                    symbol="BTCUSDT",
                    setup_type="MACRO_BREAKOUT_LONG",
                    status="BREAKOUT_ACCEPTED",
                    state=accepted_state("macro-edit") | {"status": "BREAKOUT_ACCEPTED"},
                    version=2,
                    last_evaluated_at=NOW,
                ),
            ]
        )
        await session.commit()

    response = await client.patch(
        "/chart-structures/macro-edit",
        json={"lower_price": 105, "upper_price": 105},
    )
    assert response.status_code == 200
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, "setup-edit")
        assert watched.status == "WATCH"
        assert watched.version == 3
        assert "accepted_breakout_level" not in watched.state
        assert watched.state["structure"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "route", "definition"),
    [
        (
            "RANGE",
            "/trade-plans/range-plan/range-definition",
            RangeDefinitionRow(
                id="range-def",
                trade_plan_id="range-plan",
                symbol="BTCUSDT",
                range_low=Decimal(100),
                range_high=Decimal(110),
            ),
        ),
        (
            "TREND_PULLBACK",
            "/trade-plans/trend-plan/fib-definition",
            FibDefinitionRow(
                id="fib-def",
                trade_plan_id="trend-plan",
                symbol="BTCUSDT",
                direction="LONG",
                swing_low=Decimal(100),
                swing_high=Decimal(120),
            ),
        ),
    ],
)
async def test_range_and_fib_delete_reset_linked_setup(api_client, family, route, definition):
    client, sessions = api_client
    plan_id = definition.trade_plan_id
    setup_type = f"{family}_LONG"
    async with sessions() as session:
        session.add_all(
            [
                plan(plan_id, setup_type),
                definition,
                WatchedSetupRow(
                    id=f"{family}-setup",
                    trade_plan_id=plan_id,
                    symbol="BTCUSDT",
                    setup_type=setup_type,
                    status="TRIGGER_ARMED",
                    state={
                        "symbol": "BTCUSDT",
                        "setup_type": setup_type,
                        "status": "TRIGGER_ARMED",
                        "price": 105,
                        "data_status": "CONFIRMED",
                        "structure": {"definition_id": definition.id},
                    },
                    version=7,
                    last_evaluated_at=NOW,
                ),
            ]
        )
        await session.commit()

    assert (await client.delete(route)).status_code == 204
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, f"{family}-setup")
        assert watched.status == "WATCH"
        assert watched.version == 8
        assert watched.state["structure"] == {}
        assert watched.state["blocking_reasons"][0] == "STRUCTURE_REQUIRED"


@pytest.mark.asyncio
async def test_non_geometry_label_edit_does_not_reset_lifecycle(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all(
            [
                ChartStructureRow(
                    id="macro-label",
                    symbol="BTCUSDT",
                    timeframe="4h",
                    structure_type="HORIZONTAL_ZONE",
                    label="old",
                    lower_price=Decimal(100),
                    upper_price=Decimal(100),
                    active=True,
                ),
                WatchedSetupRow(
                    id="setup-label",
                    symbol="BTCUSDT",
                    setup_type="MACRO_BREAKOUT_LONG",
                    status="BREAKOUT_ACCEPTED",
                    state=accepted_state("macro-label") | {"status": "BREAKOUT_ACCEPTED"},
                    version=2,
                    last_evaluated_at=NOW,
                ),
            ]
        )
        await session.commit()

    assert (
        await client.patch("/chart-structures/macro-label", json={"label": "new"})
    ).status_code == 200
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, "setup-label")
        assert watched.status == "BREAKOUT_ACCEPTED"
        assert watched.version == 2


@pytest.mark.asyncio
async def test_unaccepted_macro_delete_resets_current_structure_dependency(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all(
            [
                ChartStructureRow(
                    id="macro-watch",
                    symbol="BTCUSDT",
                    timeframe="4h",
                    structure_type="HORIZONTAL_ZONE",
                    lower_price=Decimal(100),
                    upper_price=Decimal(100),
                    active=True,
                ),
                WatchedSetupRow(
                    id="setup-watch",
                    symbol="BTCUSDT",
                    setup_type="MACRO_BREAKOUT_LONG",
                    status="WATCH",
                    state={
                        "status": "WATCH",
                        "structure": {"structure_id": "macro-watch"},
                        "data_status": "CONFIRMED",
                    },
                    version=1,
                    last_evaluated_at=NOW,
                ),
            ]
        )
        await session.commit()

    assert (await client.delete("/chart-structures/macro-watch")).status_code == 204
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, "setup-watch")
        assert watched.status == "WATCH"
        assert watched.version == 2
        assert watched.state["structure"] == {}
        transition = await session.scalar(
            select(ScannerTransitionRow).where(
                ScannerTransitionRow.watched_setup_id == watched.id
            )
        )
        assert transition.from_status == transition.to_status == "WATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "route", "definition", "payload"),
    [
        (
            "RANGE",
            "/trade-plans/range-edit/range-definition",
            RangeDefinitionRow(
                id="range-edit-def",
                trade_plan_id="range-edit",
                symbol="BTCUSDT",
                range_low=Decimal(100),
                range_high=Decimal(110),
            ),
            {"range_low": 101, "range_high": 111},
        ),
        (
            "TREND_PULLBACK",
            "/trade-plans/trend-edit/fib-definition",
            FibDefinitionRow(
                id="fib-edit-def",
                trade_plan_id="trend-edit",
                symbol="BTCUSDT",
                direction="LONG",
                swing_low=Decimal(100),
                swing_high=Decimal(120),
            ),
            {"direction": "LONG", "swing_low": 101, "swing_high": 121},
        ),
    ],
)
async def test_range_and_fib_geometry_edit_clear_stale_state(
    api_client, family, route, definition, payload
):
    client, sessions = api_client
    plan_id = definition.trade_plan_id
    setup_type = f"{family}_LONG"
    async with sessions() as session:
        session.add_all(
            [
                plan(plan_id, setup_type),
                definition,
                WatchedSetupRow(
                    id=f"{family}-edit-setup",
                    trade_plan_id=plan_id,
                    symbol="BTCUSDT",
                    setup_type=setup_type,
                    status="AT_LOCATION",
                    state={
                        "status": "AT_LOCATION",
                        "structure": {"definition_id": definition.id},
                        "data_status": "CONFIRMED",
                    },
                    version=3,
                    last_evaluated_at=NOW,
                ),
            ]
        )
        await session.commit()

    assert (await client.put(route, json=payload)).status_code == 200
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, f"{family}-edit-setup")
        assert watched.status == "WATCH"
        assert watched.version == 4
        assert watched.state["structure"] == {}


@pytest.mark.asyncio
async def test_replacement_structure_starts_fresh_and_monitor_stays_healthy(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all(
            [
                ChartStructureRow(
                    id="old-structure",
                    symbol="BTCUSDT",
                    timeframe="4h",
                    structure_type="HORIZONTAL_ZONE",
                    lower_price=Decimal(100),
                    upper_price=Decimal(100),
                    active=True,
                ),
                WatchedSetupRow(
                    id="replacement-setup",
                    symbol="BTCUSDT",
                    setup_type="MACRO_BREAKOUT_LONG",
                    status="BREAKOUT_ACCEPTED",
                    state=accepted_state("old-structure")
                    | {"status": "BREAKOUT_ACCEPTED"},
                    version=4,
                    last_evaluated_at=NOW,
                ),
                ScannerWatchlistRow(
                    id="watchlist-1",
                    symbol="BTCUSDT",
                    enabled=True,
                    enabled_playbooks=["MACRO_BREAKOUT_LONG"],
                    approach_tolerance_bps=Decimal(50),
                    retest_tolerance_bps=Decimal(25),
                    acceptance_bars=2,
                ),
            ]
        )
        await session.commit()

    assert (await client.delete("/chart-structures/old-structure")).status_code == 204
    assert (
        await client.post(
            "/chart-structures",
            json={
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "structure_type": "HORIZONTAL_ZONE",
                "lower_price": 105,
                "upper_price": 105,
            },
        )
    ).status_code == 201

    async def build_snapshot(symbol: str, evaluated_at: datetime):
        timeframe = ScannerTimeframeSnapshot(
            regime=Regime.UPTREND,
            close=100,
            ema12=100,
            ema21=99,
        )
        return ScannerSymbolSnapshot(
            symbol=symbol,
            current_price=100,
            evaluated_at=evaluated_at,
            one_hour=timeframe,
            four_hour=timeframe,
            completed_1h_closes=(100,),
            reaction_state=None,
            data_status=ScannerDataStatus.CONFIRMED,
        )

    monitor = SetupScannerMonitor(
        enabled=True,
        interval_seconds=30,
        concurrency=1,
        sessions=sessions,
        build_snapshot=build_snapshot,
        compose_symbol=evaluate_symbol,
    )
    await monitor.run_cycle()
    assert monitor.last_error is None
    assert monitor.failed_symbols == []
    assert monitor.evaluated_symbols == ["BTCUSDT"]
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, "replacement-setup")
        assert watched.status == "WATCH"
        assert not any(key.startswith("accepted_") for key in watched.state)
        assert watched.state["structure"]["breakout_level"] == 105
        assert watched.state["structure"]["structure_id"] != "old-structure"


@pytest.mark.asyncio
async def test_preexisting_stale_armed_reference_reconciles_without_degrading_monitor(
    api_client,
):
    client, sessions = api_client
    async with sessions() as session:
        watched = WatchedSetupRow(
            id="stale-setup",
            symbol="BTCUSDT",
            setup_type="MACRO_BREAKOUT_LONG",
            status="TRIGGER_ARMED",
            state=accepted_state("already-deleted") | {"status": "TRIGGER_ARMED"},
            version=9,
            last_evaluated_at=NOW,
        )
        session.add_all(
            [
                watched,
                ScannerWatchlistRow(
                    id="stale-watchlist",
                    symbol="BTCUSDT",
                    enabled=True,
                    enabled_playbooks=["MACRO_BREAKOUT_LONG"],
                    approach_tolerance_bps=Decimal(50),
                    retest_tolerance_bps=Decimal(25),
                    acceptance_bars=2,
                ),
                TriggerAttemptRow(
                    id="historical-attempt",
                    watched_setup_id=watched.id,
                    symbol="BTCUSDT",
                    setup_type=watched.setup_type,
                    arm_key="TRANSITION:stale",
                    arm_source="ARM_TRANSITION",
                    arm_transition_id="stale",
                    armed_at=NOW,
                    reference_level=Decimal(100),
                    reference_source="MACRO_BREAKOUT_LEVEL",
                    reference_metadata={"structure_id": "already-deleted"},
                    retest_tolerance_bps=Decimal(25),
                    failure_tolerance_bps=Decimal(25),
                    state="WAITING",
                    result={"state": "WAITING"},
                    version=1,
                    first_evaluated_at=NOW,
                    last_evaluated_at=NOW,
                ),
            ]
        )
        await session.commit()

    async def build_snapshot(symbol: str, evaluated_at: datetime):
        timeframe = ScannerTimeframeSnapshot(
            regime=Regime.UPTREND,
            close=100,
            ema12=100,
            ema21=99,
        )
        return ScannerSymbolSnapshot(
            symbol=symbol,
            current_price=100,
            evaluated_at=evaluated_at,
            one_hour=timeframe,
            four_hour=timeframe,
            completed_1h_closes=(100,),
            reaction_state=None,
            data_status=ScannerDataStatus.CONFIRMED,
        )

    monitor = SetupScannerMonitor(
        enabled=True,
        interval_seconds=30,
        concurrency=1,
        sessions=sessions,
        build_snapshot=build_snapshot,
        compose_symbol=evaluate_symbol,
    )
    await monitor.run_cycle()

    assert monitor.last_error is None
    assert monitor.failed_symbols == []
    assert monitor.evaluated_symbols == ["BTCUSDT"]
    assert (await client.get("/trigger/current?watched_setup_id=stale-setup")).json() == []
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, "stale-setup")
        assert watched.status == "WATCH"
        assert watched.version == 10
        assert watched.state["reconciliation_reason"] == (
            "STALE_STRUCTURE_REFERENCE_RECONCILED"
        )
        assert not any(key.startswith("accepted_") for key in watched.state)
        assert await session.get(TriggerAttemptRow, "historical-attempt") is not None
        transition = await session.scalar(
            select(ScannerTransitionRow).where(
                ScannerTransitionRow.watched_setup_id == watched.id,
                ScannerTransitionRow.version == 10,
            )
        )
        assert transition.from_status == "TRIGGER_ARMED"
        assert transition.to_status == "WATCH"
        assert transition.state_after["reconciliation_reason"] == (
            "STALE_STRUCTURE_REFERENCE_RECONCILED"
        )

    replacement = await client.post(
        "/chart-structures",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "structure_type": "HORIZONTAL_ZONE",
            "lower_price": 105,
            "upper_price": 105,
        },
    )
    assert replacement.status_code == 201
    await monitor.run_cycle()
    async with sessions() as session:
        watched = await session.get(WatchedSetupRow, "stale-setup")
        assert watched.state["structure"]["structure_id"] == replacement.json()["id"]
        assert "reconciliation_reason" not in watched.state
