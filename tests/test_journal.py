import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    Base,
    ExecutionRuleRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    WatchedSetupRow,
)


@pytest_asyncio.fixture
async def journal_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/journal.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_trade_plan_snapshot_event_and_review(journal_client):
    plan_body = {
        "symbol": "ethusdt",
        "side": "SHORT",
        "setup_type": "TREND_PULLBACK",
        "thesis": "Reject planned resistance",
        "hard_invalidation": 4000,
        "max_risk_percent": 0.01,
        "entry_probe_plan": {"probe_price": 3900},
        "add_conditions": [{"type": "SELLER_CONFIRMATION"}],
        "target_ladder": [{"price": 3700, "reduction_percent": 0.5, "ordering": 1}],
        "execution_rules": [
            {
                "action": "ADD",
                "rule_type": "CONFIRMATION_REQUIRED",
                "description": "Seller confirmation required",
            }
        ],
    }
    response = await journal_client.post("/trade-plans", json=plan_body)
    assert response.status_code == 201
    plan = response.json()
    assert plan["symbol"] == "ETHUSDT"

    snapshot = await journal_client.post(
        f"/trade-plans/{plan['id']}/snapshots",
        json={
            "symbol": "ethusdt",
            "price": 3900,
            "action_considered": "ADD",
            "state": {"regime_4h": "BEARISH", "risk_budget_status": "PASS"},
            "evidence_missing": ["seller confirmation"],
        },
    )
    assert snapshot.status_code == 201
    assert snapshot.json()["symbol"] == "ETHUSDT"
    snapshot_id = snapshot.json()["id"]

    event_body = {
        "symbol": "ethusdt",
        "event_type": "HOLD",
        "bybit_execution_id": "fill-1",
        "decision_snapshot_id": snapshot_id,
        "planned": True,
        "confidence_status": "CONFIRMED",
    }
    event = await journal_client.post(
        f"/trade-plans/{plan['id']}/execution-events", json=event_body
    )
    assert event.status_code == 201
    assert event.json()["symbol"] == "ETHUSDT"
    assert (
        await journal_client.post(f"/trade-plans/{plan['id']}/execution-events", json=event_body)
    ).status_code == 409

    review = await journal_client.post(
        f"/trade-plans/{plan['id']}/review",
        json={"realized_r": -1, "facts": {"invalidation_respected": True}},
    )
    assert review.status_code == 201
    assert (await journal_client.get(f"/trade-plans/{plan['id']}/snapshots")).json()[0][
        "id"
    ] == snapshot_id


@pytest.mark.asyncio
async def test_snapshot_symbol_must_match_plan(journal_client):
    plan = (
        await journal_client.post(
            "/trade-plans",
            json={
                "symbol": "BNBUSDT",
                "side": "LONG",
                "setup_type": "RANGE_LONG",
                "thesis": "Range low reclaim",
                "hard_invalidation": 500,
                "max_risk_percent": 0.01,
            },
        )
    ).json()
    response = await journal_client.post(
        f"/trade-plans/{plan['id']}/snapshots",
        json={"symbol": "ETHUSDT", "price": 600, "action_considered": "WAIT"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_trade_plan_and_rules_roll_back_together(journal_client):
    def fail_rule_insert(*_):
        raise RuntimeError("deterministic rule persistence failure")

    event.listen(ExecutionRuleRow, "before_insert", fail_rule_insert)
    try:
        with pytest.raises(RuntimeError, match="deterministic rule persistence failure"):
            await journal_client.post(
                "/trade-plans",
                json={
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "setup_type": "TREND_PULLBACK",
                    "thesis": "Atomic creation",
                    "hard_invalidation": "90000.00000001",
                    "max_risk_percent": "0.01",
                    "execution_rules": [
                        {
                            "action": "PROBE",
                            "rule_type": "CONTEXT",
                            "description": "Context",
                        }
                    ],
                },
            )
    finally:
        event.remove(ExecutionRuleRow, "before_insert", fail_rule_insert)
    plans = await journal_client.get("/trade-plans", params={"symbol": "BTCUSDT"})
    assert plans.json() == []


@pytest.mark.asyncio
async def test_execution_event_rejects_missing_snapshot(journal_client):
    plan = await _create_plan(journal_client, "BNBUSDT")
    response = await journal_client.post(
        f"/trade-plans/{plan['id']}/execution-events",
        json={
            "symbol": "BNBUSDT",
            "event_type": "HOLD",
            "decision_snapshot_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_execution_event_rejects_cross_plan_snapshot(journal_client):
    first = await _create_plan(journal_client, "BNBUSDT")
    second = await _create_plan(journal_client, "ETHUSDT")
    snapshot = await journal_client.post(
        f"/trade-plans/{first['id']}/snapshots",
        json={"symbol": "BNBUSDT", "price": "600.1", "action_considered": "WAIT"},
    )
    response = await journal_client.post(
        f"/trade-plans/{second['id']}/execution-events",
        json={
            "symbol": "ETHUSDT",
            "event_type": "HOLD",
            "decision_snapshot_id": snapshot.json()["id"],
        },
    )
    assert response.status_code == 422


async def _create_plan(client, symbol):
    response = await client.post(
        "/trade-plans",
        json={
            "symbol": symbol,
            "side": "LONG",
            "setup_type": "RANGE_LONG",
            "thesis": "Test plan",
            "hard_invalidation": "1.00000001",
            "max_risk_percent": "0.01",
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_preplan_market_definitions_allow_no_plan_and_normalize_symbols(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/definitions.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        records = [
            WatchedSetupRow(symbol="btcusdt", setup_type="RANGE_LONG", status="WATCHING"),
            FibDefinitionRow(
                symbol="ethusdt", direction="LONG", swing_low="100.00000001", swing_high="200"
            ),
            RangeDefinitionRow(symbol="bnbusdt", range_low="500.1", range_high="600.2"),
        ]
        session.add_all(records)
        await session.commit()
        assert [record.trade_plan_id for record in records] == [None, None, None]
        assert [record.symbol for record in records] == ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    await engine.dispose()
