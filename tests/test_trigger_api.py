from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.api.trigger import set_trigger_status_provider
from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    Base,
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TriggerAttemptRow,
    TriggerTransitionRow,
    WatchedSetupRow,
)

ARMED_AT = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)


def result_payload(*, state="WAITING", evaluated_at=None):
    return {
        "symbol": "BTCUSDT",
        "setup_type": "RANGE_LONG",
        "side": "long",
        "pattern": "DEVIATION_RECLAIM",
        "state": state,
        "reference_level": 100,
        "armed_at": ARMED_AT.isoformat(),
        "evaluated_at": (evaluated_at or ARMED_AT + timedelta(minutes=15)).isoformat(),
        "trigger_confirmed": state == "CONFIRMED",
        "anchor_bar_end_ms": None,
        "confirmation_bar_end_ms": None,
        "local_15m_acceptance": None,
        "reaction_state": None,
        "reaction_supportive": None,
        "anchor_high": None,
        "anchor_low": None,
        "anchor_close": None,
        "confirmation_close": None,
        "evidence_present": [],
        "evidence_missing": ["FIVE_MINUTE_RECLAIM"],
        "blocking_reasons": [],
        "next_conditions": ["WAIT_FOR_RECLAIM"],
    }


def watchlist(*, enabled=True, playbooks=None):
    return ScannerWatchlistRow(
        id="watch-1",
        symbol="BTCUSDT",
        enabled=enabled,
        enabled_playbooks=playbooks or ["RANGE"],
        approach_tolerance_bps=Decimal(50),
        retest_tolerance_bps=Decimal(25),
        acceptance_bars=2,
    )


def watched(*, status="TRIGGER_ARMED", version=1):
    return WatchedSetupRow(
        id="setup-1",
        symbol="BTCUSDT",
        setup_type="RANGE_LONG",
        status=status,
        state={"location": {"range_low": 100, "range_high": 110}},
        version=version,
        created_at=ARMED_AT,
    )


def attempt(
    *,
    attempt_id="attempt-1",
    arm_key=None,
    arm_transition_id=None,
    armed_at=ARMED_AT,
    state="WAITING",
    created_at=None,
):
    return TriggerAttemptRow(
        id=attempt_id,
        watched_setup_id="setup-1",
        symbol="BTCUSDT",
        setup_type="RANGE_LONG",
        arm_key=arm_key or f"BASELINE:setup-1:{ARMED_AT.isoformat()}",
        arm_source=(
            "ARM_TRANSITION" if arm_transition_id else "FIRST_OBSERVATION_BASELINE"
        ),
        arm_transition_id=arm_transition_id,
        armed_at=armed_at,
        reference_level=Decimal(100),
        reference_source="RANGE_LOW",
        reference_metadata={},
        retest_tolerance_bps=Decimal(25),
        failure_tolerance_bps=Decimal(25),
        state=state,
        result=result_payload(state=state, evaluated_at=armed_at + timedelta(minutes=15)),
        version=1,
        first_evaluated_at=armed_at + timedelta(minutes=15),
        last_evaluated_at=armed_at + timedelta(minutes=15),
        created_at=created_at or armed_at,
        updated_at=created_at or armed_at,
    )


@pytest_asyncio.fixture
async def api_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trigger-api.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    set_trigger_status_provider(None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sessions
    set_trigger_status_provider(None)
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_status_defaults_when_provider_missing(api_client):
    client, _ = api_client
    response = await client.get("/trigger/status")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "running": False,
        "interval_seconds": 15.0,
        "last_cycle_started_at": None,
        "last_cycle_completed_at": None,
        "last_success_at": None,
        "last_error": None,
        "last_cycle_duration_ms": None,
        "armed_candidate_count": 0,
        "eligible_candidate_count": 0,
        "evaluation_count": 0,
        "persisted_count": 0,
        "confirmed_count": 0,
        "terminal_count": 0,
        "evaluated_symbols": [],
        "failed_symbols": [],
    }


@pytest.mark.asyncio
async def test_status_serializes_provider(api_client):
    client, _ = api_client
    payload = {
        "enabled": True,
        "running": True,
        "interval_seconds": 12,
        "last_cycle_started_at": ARMED_AT,
        "last_cycle_completed_at": ARMED_AT + timedelta(seconds=1),
        "last_success_at": ARMED_AT,
        "last_error": "ETHUSDT: offline",
        "last_cycle_duration_ms": 12.5,
        "armed_candidate_count": 3,
        "eligible_candidate_count": 2,
        "evaluation_count": 1,
        "persisted_count": 1,
        "confirmed_count": 1,
        "terminal_count": 1,
        "evaluated_symbols": ["BTCUSDT"],
        "failed_symbols": ["ETHUSDT"],
    }
    set_trigger_status_provider(lambda: payload)

    response = await client.get("/trigger/status")

    assert response.status_code == 200
    assert response.json()["confirmed_count"] == 1
    assert response.json()["failed_symbols"] == ["ETHUSDT"]


@pytest.mark.asyncio
async def test_attempt_list_order_and_filters(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add(watched())
        session.add_all(
            [
                attempt(attempt_id="older", state="WAITING"),
                attempt(
                    attempt_id="newer",
                    arm_key="TRANSITION:new",
                    arm_transition_id="new",
                    armed_at=ARMED_AT + timedelta(hours=1),
                    state="CONFIRMED",
                ),
            ]
        )
        await session.commit()

    response = await client.get("/trigger/attempts")
    filtered = await client.get(
        "/trigger/attempts",
        params={
            "symbol": "btcusdt",
            "setup_type": "RANGE_LONG",
            "state": "CONFIRMED",
            "watched_setup_id": "setup-1",
            "arm_key": "TRANSITION:new",
        },
    )

    assert [item["id"] for item in response.json()] == ["newer", "older"]
    assert [item["id"] for item in filtered.json()] == ["newer"]
    assert (await client.get("/trigger/attempts?limit=0")).status_code == 422
    assert (await client.get("/trigger/attempts?limit=501")).status_code == 422


@pytest.mark.asyncio
async def test_attempt_detail_and_missing(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add(watched())
        session.add(attempt())
        await session.commit()

    found = await client.get("/trigger/attempts/attempt-1")
    missing = await client.get("/trigger/attempts/missing")

    assert found.status_code == 200
    assert found.json()["result"]["pattern"] == "DEVIATION_RECLAIM"
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_transition_filtering_and_order(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add(watched())
        session.add(attempt())
        for version, state, timestamp in [
            (2, "DEVELOPING", ARMED_AT + timedelta(minutes=20)),
            (3, "CONFIRMED", ARMED_AT + timedelta(minutes=30)),
        ]:
            session.add(
                TriggerTransitionRow(
                    id=f"transition-{version}",
                    trigger_attempt_id="attempt-1",
                    symbol="BTCUSDT",
                    setup_type="RANGE_LONG",
                    from_state="WAITING" if version == 2 else "DEVELOPING",
                    to_state=state,
                    timestamp=timestamp,
                    result_before=result_payload(),
                    result_after=result_payload(state=state),
                    version=version,
                )
            )
        await session.commit()

    response = await client.get(
        "/trigger/transitions",
        params={
            "trigger_attempt_id": "attempt-1",
            "symbol": "btcusdt",
            "setup_type": "RANGE_LONG",
            "to_state": "CONFIRMED",
        },
    )
    all_rows = await client.get("/trigger/transitions")

    assert [item["id"] for item in response.json()] == ["transition-3"]
    assert [item["id"] for item in all_rows.json()] == [
        "transition-3",
        "transition-2",
    ]


@pytest.mark.asyncio
async def test_current_eligible_with_and_without_attempt(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all([watchlist(), watched(), attempt()])
        await session.commit()

    response = await client.get("/trigger/current")
    current = response.json()[0]
    assert current["eligible"] is True
    assert current["attempt"]["id"] == "attempt-1"
    assert current["terminal"] is False

    async with sessions() as session:
        persisted = await session.get(TriggerAttemptRow, "attempt-1")
        await session.delete(persisted)
        await session.commit()
    current = (await client.get("/trigger/current")).json()[0]
    assert current["eligible"] is True
    assert current["attempt"] is None


@pytest.mark.asyncio
async def test_current_omits_non_armed_setup(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all([watchlist(), watched(status="WATCH")])
        await session.commit()

    assert (await client.get("/trigger/current")).json() == []


def arm_transition(transition_id, version, timestamp):
    return ScannerTransitionRow(
        id=transition_id,
        watched_setup_id="setup-1",
        symbol="BTCUSDT",
        setup_type="RANGE_LONG",
        from_status="WATCH",
        to_status="TRIGGER_ARMED",
        timestamp=timestamp,
        state_before={},
        state_after={"location": {"range_low": 100, "range_high": 110}},
        version=version,
    )


@pytest.mark.asyncio
async def test_current_rearm_ignores_historical_attempt_and_uses_exact_new_arm(api_client):
    client, sessions = api_client
    arm_b_at = ARMED_AT + timedelta(hours=2)
    async with sessions() as session:
        session.add_all(
            [
                watchlist(),
                watched(version=4),
                arm_transition("arm-a", 2, ARMED_AT),
                arm_transition("arm-b", 4, arm_b_at),
                attempt(
                    attempt_id="attempt-a",
                    arm_key="TRANSITION:arm-a",
                    arm_transition_id="arm-a",
                    state="CONFIRMED",
                ),
            ]
        )
        await session.commit()

    current = (await client.get("/trigger/current")).json()[0]
    assert current["arm_key"] == "TRANSITION:arm-b"
    assert current["attempt"] is None
    assert current["terminal"] is False

    async with sessions() as session:
        session.add(
            attempt(
                attempt_id="attempt-b",
                arm_key="TRANSITION:arm-b",
                arm_transition_id="arm-b",
                armed_at=arm_b_at,
                state="CONFIRMED",
            )
        )
        await session.commit()
    current = (await client.get("/trigger/current")).json()[0]
    assert current["attempt"]["id"] == "attempt-b"
    assert current["terminal"] is True


@pytest.mark.asyncio
async def test_current_surfaces_invalid_context_and_filters(api_client):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all([watchlist(playbooks=["TREND_PULLBACK"]), watched()])
        await session.commit()

    current = (
        await client.get(
            "/trigger/current",
            params={
                "symbol": "btcusdt",
                "setup_type": "RANGE_LONG",
                "watched_setup_id": "setup-1",
                "limit": 1,
            },
        )
    ).json()[0]

    assert current["eligible"] is False
    assert current["blocking_reason"] == "SETUP_DISABLED"
    assert current["attempt"] is None


@pytest.mark.asyncio
async def test_current_endpoint_performs_no_market_evaluation_or_persistence(
    api_client, monkeypatch
):
    client, sessions = api_client
    async with sessions() as session:
        session.add_all([watchlist(), watched()])
        await session.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("read-only API must not evaluate, fetch, or persist")

    monkeypatch.setattr(
        "trading_copilot.services.trigger_snapshot.build_trigger_snapshot", forbidden
    )
    monkeypatch.setattr(
        "trading_copilot.services.trigger_engine.evaluate_lower_timeframe_trigger",
        forbidden,
    )
    monkeypatch.setattr(
        "trading_copilot.services.trigger_persistence.persist_trigger_result", forbidden
    )

    response = await client.get("/trigger/current")

    assert response.status_code == 200
    assert response.json()[0]["attempt"] is None
