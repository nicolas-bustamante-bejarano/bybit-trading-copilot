from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import trading_copilot.main as main_module
from trading_copilot.cli import migrate_database
from trading_copilot.config import Settings, normalize_database_url
from trading_copilot.domain.account import NormalizedAccount, NormalizedPosition
from trading_copilot.domain.models import Side
from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import Base, TradePlanRow
from trading_copilot.services.structural_risk import structural_risk_summary


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("postgresql://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("postgresql+asyncpg://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("sqlite+aiosqlite:///local.db", "sqlite+aiosqlite:///local.db"),
    ],
)
def test_database_url_normalization(raw, expected):
    assert normalize_database_url(raw) == expected


def test_production_requires_postgres():
    with pytest.raises(ValidationError, match="requires a PostgreSQL DATABASE_URL"):
        Settings(app_env="prod", database_url="sqlite+aiosqlite:///local.db", _env_file=None)
    configured = Settings(
        app_env="prod", database_url="postgresql://u:p@host/db", _env_file=None
    )
    assert configured.database_url.startswith("postgresql+asyncpg://")


def account(*positions):
    return NormalizedAccount(
        equity_usdt=5000,
        max_group_risk_pct=0.02,
        risk_budget_usdt=100,
        total_structural_risk_usdt=0,
        total_structural_risk_pct=0,
        within_risk_budget=True,
        positions=list(positions),
    )


def position(symbol="BNBUSDT", stop=None, entry=600):
    return NormalizedPosition(
        symbol=symbol,
        side=Side.LONG,
        quantity=2,
        average_entry=entry,
        mark_price=610,
        stop_loss=stop,
        structural_risk_usdt=2 * abs(entry - stop) if stop else None,
        protected=stop is not None,
    )


def plan(symbol="BNBUSDT", invalidation="550"):
    return TradePlanRow(
        id=f"plan-{symbol}",
        symbol=symbol,
        side="LONG",
        setup_type="RANGE_LONG",
        thesis="Range support",
        lifecycle_status="ACTIVE",
        hard_invalidation=Decimal(invalidation),
        max_risk_percent=Decimal("0.02"),
    )


def test_plan_risk_is_canonical_and_preserves_provenance():
    current = account(position(), position("ETHUSDT", 1900, 2000))
    summary = structural_risk_summary(current, {"BNBUSDT": plan()})
    assert summary["known_structural_risk_usdt"] == Decimal(300)
    assert summary["risk_policy_status"] == "BREACH"
    assert summary["unknown_symbols"] == []
    assert summary["provenance"] == {
        "BNBUSDT": "execution_plan",
        "ETHUSDT": "exchange_order",
    }


def test_known_subtotal_with_unknown_exposure_is_indeterminate():
    summary = structural_risk_summary(
        account(position(), position("ETHUSDT")), {"BNBUSDT": plan()}
    )
    assert summary["known_structural_risk_usdt"] == Decimal(100)
    assert summary["total_structural_risk_pct"] is None
    assert summary["risk_policy_status"] == "INDETERMINATE"
    assert summary["unknown_symbols"] == ["ETHUSDT"]


@pytest.mark.asyncio
async def test_database_migration_preserves_ids_and_fails_safely_on_repeat(tmp_path):
    source_url = f"sqlite+aiosqlite:///{tmp_path}/source.db"
    target_url = f"sqlite+aiosqlite:///{tmp_path}/target.db"
    for url in (source_url, target_url):
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()
    source_engine = create_async_engine(source_url)
    sessions = async_sessionmaker(source_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(plan())
        await session.commit()
    await source_engine.dispose()
    result = await migrate_database(source_url, target_url)
    assert result["trade_plans"] == 1
    with pytest.raises(RuntimeError, match="already contain data"):
        await migrate_database(source_url, target_url)


@pytest.mark.asyncio
async def test_health_does_not_depend_on_bybit():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest_asyncio.fixture
async def portfolio_client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/portfolio.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr(
        main_module,
        "_normalized_account",
        lambda: pytest.fail("async account stub was not installed"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sessions, monkeypatch
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_portfolio_live_uses_same_plan_backed_risk(portfolio_client):
    client, sessions, monkeypatch = portfolio_client
    current = account(position())

    async def account_stub():
        return current

    monkeypatch.setattr(main_module, "_normalized_account", account_stub)
    async with sessions() as session:
        session.add(plan())
        await session.commit()
    payload = (await client.get("/portfolio/live")).json()
    assert Decimal(payload["known_structural_risk_usdt"]) == Decimal(100)
    assert Decimal(payload["known_structural_risk_pct"]) == Decimal("0.02")
    assert payload["risk_policy_status"] == "PASS"
    assert payload["unknown_symbols"] == []
    assert payload["provenance"] == {"BNBUSDT": "execution_plan"}
