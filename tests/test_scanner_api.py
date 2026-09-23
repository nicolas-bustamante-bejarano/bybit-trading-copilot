from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import trading_copilot.api.scanner as scanner_api
import trading_copilot.main as main_module
from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    Base,
    ScannerTransitionRow,
    ScannerWatchlistRow,
    WatchedSetupRow,
)
from trading_copilot.services.scanner_monitor import ScannerMonitorStatus

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def api_context(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scanner-api.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    scanner_api.set_status_provider(None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sessions
    scanner_api.set_status_provider(None)
    app.dependency_overrides.clear()
    await engine.dispose()


def watchlist_payload(symbol="BTCUSDT"):
    return {
        "symbol": symbol,
        "enabled": True,
        "enabled_playbooks": ["TREND_PULLBACK", "RANGE", "MACRO_BREAKOUT"],
        "approach_tolerance_bps": 50,
        "retest_tolerance_bps": 25,
        "acceptance_bars": 2,
    }


def setup_row(
    setup_id,
    symbol,
    setup_type,
    status,
    *,
    state=None,
    version=1,
):
    return WatchedSetupRow(
        id=setup_id,
        symbol=symbol,
        setup_type=setup_type,
        status=status,
        state=state or {},
        version=version,
        last_evaluated_at=NOW,
    )


def transition_row(
    transition_id,
    watched_setup_id,
    symbol,
    setup_type,
    timestamp,
    version,
):
    return ScannerTransitionRow(
        id=transition_id,
        watched_setup_id=watched_setup_id,
        symbol=symbol,
        setup_type=setup_type,
        from_status="WATCH",
        to_status="APPROACHING_LOCATION",
        timestamp=timestamp,
        state_before={"phase": "before", "version": version - 1},
        state_after={"phase": "after", "version": version},
        version=version,
    )


@pytest.mark.asyncio
async def test_status_defaults_and_injected_provider(api_context):
    client, _ = api_context
    default = await client.get("/scanner/status")
    assert default.status_code == 200
    assert default.json() == {
        "enabled": False,
        "running": False,
        "interval_seconds": 30.0,
        "last_cycle_started_at": None,
        "last_cycle_completed_at": None,
        "last_success_at": None,
        "last_error": None,
        "last_cycle_duration_ms": None,
        "watchlist_count": 0,
        "setup_count": 0,
        "evaluated_symbols": [],
        "failed_symbols": [],
    }

    scanner_api.set_status_provider(
        lambda: ScannerMonitorStatus(
            enabled=True,
            running=True,
            interval_seconds=15,
            last_cycle_started_at=NOW,
            last_cycle_completed_at=NOW,
            last_success_at=NOW,
            last_error="BTCUSDT: unavailable",
            last_cycle_duration_ms=12.5,
            watchlist_count=2,
            setup_count=5,
            evaluated_symbols=["ETHUSDT"],
            failed_symbols=["BTCUSDT"],
        )
    )
    response = await client.get("/scanner/status")

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["running"] is True
    assert response.json()["evaluated_symbols"] == ["ETHUSDT"]
    assert response.json()["failed_symbols"] == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_lifespan_installs_created_monitor_status_provider(api_context, monkeypatch):
    client, _ = api_context

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def status(self):
            return ScannerMonitorStatus(
                enabled=False,
                running=False,
                interval_seconds=37,
                last_cycle_started_at=None,
                last_cycle_completed_at=None,
                last_success_at=None,
                last_error=None,
                last_cycle_duration_ms=None,
                watchlist_count=0,
                setup_count=0,
                evaluated_symbols=[],
                failed_symbols=[],
            )

    monkeypatch.setattr(main_module, "SetupScannerMonitor", FakeMonitor)
    monkeypatch.setattr(main_module.settings, "setup_scanner_enabled", False)
    monkeypatch.setattr(main_module.settings, "live_stream_enabled", False)
    monkeypatch.setattr(main_module.settings, "state_change_monitor_enabled", False)

    async with main_module.lifespan(app):
        response = await client.get("/scanner/status")

    assert response.json()["interval_seconds"] == 37


@pytest.mark.asyncio
async def test_watchlist_create_normalizes_symbol_playbooks_and_serializes_config(api_context):
    client, sessions = api_context
    payload = watchlist_payload(" btcusdt ")
    payload["enabled_playbooks"] = [
        "range_long",
        "trend_pullback",
        "RANGE",
        "range_long",
    ]
    response = await client.post("/scanner/watchlist", json=payload)

    assert response.status_code == 201
    assert response.json()["symbol"] == "BTCUSDT"
    assert response.json()["enabled_playbooks"] == [
        "TREND_PULLBACK",
        "RANGE",
        "RANGE_LONG",
    ]
    assert response.json()["approach_tolerance_bps"] == 50.0
    async with sessions() as session:
        assert await session.scalar(select(func.count(WatchedSetupRow.id))) == 0


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"enabled_playbooks": ["UNKNOWN"]}, "unsupported scanner playbook"),
        ({"approach_tolerance_bps": -1}, "greater than or equal to 0"),
        ({"retest_tolerance_bps": -1}, "greater than or equal to 0"),
        ({"acceptance_bars": 0}, "greater than or equal to 1"),
    ],
)
@pytest.mark.asyncio
async def test_watchlist_create_rejects_invalid_configuration(api_context, change, detail):
    client, _ = api_context
    payload = watchlist_payload()
    payload.update(change)

    response = await client.post("/scanner/watchlist", json=payload)

    assert response.status_code == 422
    assert detail in response.text


@pytest.mark.asyncio
async def test_duplicate_watchlist_symbol_returns_conflict(api_context):
    client, _ = api_context
    assert (await client.post("/scanner/watchlist", json=watchlist_payload())).status_code == 201

    duplicate = await client.post("/scanner/watchlist", json=watchlist_payload("btcusdt"))

    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_watchlist_list_is_symbol_sorted_with_stored_config(api_context):
    client, _ = api_context
    eth = watchlist_payload("ETHUSDT")
    eth["enabled"] = False
    eth["acceptance_bars"] = 3
    await client.post("/scanner/watchlist", json=eth)
    await client.post("/scanner/watchlist", json=watchlist_payload("BTCUSDT"))

    response = await client.get("/scanner/watchlist")

    assert response.status_code == 200
    assert [row["symbol"] for row in response.json()] == ["BTCUSDT", "ETHUSDT"]
    assert response.json()[1]["enabled"] is False
    assert response.json()[1]["acceptance_bars"] == 3


@pytest.mark.asyncio
async def test_watchlist_patch_updates_all_mutable_fields_and_empty_is_unchanged(api_context):
    client, _ = api_context
    await client.post("/scanner/watchlist", json=watchlist_payload())
    updates = {
        "enabled": False,
        "enabled_playbooks": ["macro_breakout_short", "range"],
        "approach_tolerance_bps": 75,
        "retest_tolerance_bps": 10,
        "acceptance_bars": 3,
    }

    response = await client.patch("/scanner/watchlist/btcusdt", json=updates)
    unchanged = await client.patch("/scanner/watchlist/BTCUSDT", json={})

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["enabled_playbooks"] == ["RANGE", "MACRO_BREAKOUT_SHORT"]
    assert response.json()["approach_tolerance_bps"] == 75.0
    assert response.json()["retest_tolerance_bps"] == 10.0
    assert response.json()["acceptance_bars"] == 3
    assert unchanged.json() == response.json()


@pytest.mark.asyncio
async def test_watchlist_patch_unknown_and_invalid_leave_data_unchanged(api_context):
    client, _ = api_context
    created = await client.post("/scanner/watchlist", json=watchlist_payload())
    assert (await client.patch("/scanner/watchlist/ETHUSDT", json={"enabled": False})).status_code == 404

    invalid = await client.patch(
        "/scanner/watchlist/BTCUSDT", json={"enabled": False, "acceptance_bars": 0}
    )
    current = (await client.get("/scanner/watchlist")).json()[0]

    assert invalid.status_code == 422
    assert current == created.json()


@pytest.mark.asyncio
async def test_watchlist_delete_preserves_setup_and_transition_history(api_context):
    client, sessions = api_context
    await client.post("/scanner/watchlist", json=watchlist_payload())
    async with sessions() as session:
        setup = setup_row(
            "setup-1", "BTCUSDT", "TREND_PULLBACK_LONG", "WATCH", state={"kept": True}
        )
        session.add(setup)
        await session.commit()
        session.add(
            transition_row(
                "transition-1",
                setup.id,
                setup.symbol,
                setup.setup_type,
                NOW,
                2,
            )
        )
        await session.commit()

    deleted = await client.delete("/scanner/watchlist/btcusdt")
    missing = await client.delete("/scanner/watchlist/BTCUSDT")
    async with sessions() as session:
        assert await session.scalar(select(func.count(ScannerWatchlistRow.id))) == 0
        assert await session.scalar(select(func.count(WatchedSetupRow.id))) == 1
        assert await session.scalar(select(func.count(ScannerTransitionRow.id))) == 1

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert (await client.get("/scanner/watchlist")).json() == []


@pytest.mark.asyncio
async def test_setup_list_filters_combine_and_order_deterministically(api_context):
    client, sessions = api_context
    async with sessions() as session:
        session.add_all(
            [
                setup_row("3", "ETHUSDT", "RANGE_LONG", "AT_LOCATION"),
                setup_row("2", "BTCUSDT", "TREND_PULLBACK_SHORT", "WATCH"),
                setup_row(
                    "1",
                    "BTCUSDT",
                    "TREND_PULLBACK_LONG",
                    "WATCH",
                    state={"canonical": {"unchanged": True}},
                ),
            ]
        )
        await session.commit()

    all_rows = await client.get("/scanner/setups")
    filtered = await client.get(
        "/scanner/setups",
        params={
            "symbol": "btcusdt",
            "setup_type": "TREND_PULLBACK_LONG",
            "status": "WATCH",
        },
    )

    assert [row["id"] for row in all_rows.json()] == ["1", "2", "3"]
    assert [row["id"] for row in filtered.json()] == ["1"]


@pytest.mark.parametrize(
    "path",
    [
        "/scanner/setups?setup_type=UNKNOWN",
        "/scanner/setups?status=UNKNOWN",
        "/scanner/setups?limit=0",
        "/scanner/setups?limit=501",
    ],
)
@pytest.mark.asyncio
async def test_setup_list_rejects_invalid_filters(api_context, path):
    client, _ = api_context
    assert (await client.get(path)).status_code == 422


@pytest.mark.asyncio
async def test_setup_detail_returns_persisted_state_without_recomputation(api_context):
    client, sessions = api_context
    state = {"structure": {"breakout_level": 100}, "accepted_at": NOW.isoformat()}
    async with sessions() as session:
        session.add(
            setup_row(
                "setup-1",
                "BTCUSDT",
                "MACRO_BREAKOUT_LONG",
                "RETEST_PENDING",
                state=state,
                version=4,
            )
        )
        await session.commit()

    response = await client.get("/scanner/setups/btcusdt/MACRO_BREAKOUT_LONG")
    missing = await client.get("/scanner/setups/ETHUSDT/MACRO_BREAKOUT_LONG")

    assert response.status_code == 200
    assert response.json()["state"] == state
    assert response.json()["version"] == 4
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_transition_list_orders_newest_first_and_preserves_snapshots(api_context):
    client, sessions = api_context
    async with sessions() as session:
        first = setup_row("setup-1", "BTCUSDT", "TREND_PULLBACK_LONG", "WATCH")
        second = setup_row("setup-2", "ETHUSDT", "RANGE_LONG", "AT_LOCATION")
        session.add_all([first, second])
        await session.commit()
        session.add_all(
            [
                transition_row(
                    "old", first.id, first.symbol, first.setup_type, NOW, 2
                ),
                transition_row(
                    "new",
                    second.id,
                    second.symbol,
                    second.setup_type,
                    NOW + timedelta(minutes=1),
                    3,
                ),
            ]
        )
        await session.commit()

    all_rows = await client.get("/scanner/transitions")
    by_symbol = await client.get("/scanner/transitions?symbol=btcusdt")
    by_type = await client.get("/scanner/transitions?setup_type=RANGE_LONG")
    by_id = await client.get(f"/scanner/transitions?watched_setup_id={first.id}")

    assert [row["id"] for row in all_rows.json()] == ["new", "old"]
    assert [row["id"] for row in by_symbol.json()] == ["old"]
    assert [row["id"] for row in by_type.json()] == ["new"]
    assert [row["id"] for row in by_id.json()] == ["old"]
    assert by_id.json()[0]["state_before"] == {"phase": "before", "version": 1}
    assert by_id.json()[0]["state_after"] == {"phase": "after", "version": 2}
    assert by_id.json()[0]["version"] == 2


@pytest.mark.parametrize("limit", [0, 501])
@pytest.mark.asyncio
async def test_transition_list_validates_limit(api_context, limit):
    client, _ = api_context
    assert (await client.get(f"/scanner/transitions?limit={limit}")).status_code == 422
