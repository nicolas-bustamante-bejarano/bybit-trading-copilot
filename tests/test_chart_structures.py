import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import Base


@pytest_asyncio.fixture
async def journal_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/structures.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_trendline_and_zone_can_be_created_updated_deactivated_and_deleted(journal_client):
    trendline = await journal_client.post(
        "/chart-structures",
        json={"symbol": "bonkusdt", "timeframe": "4h", "structure_type": "TRENDLINE", "label": "macro", "anchor_one_time": 1000, "anchor_one_price": "0.01", "anchor_two_time": 2000, "anchor_two_price": "0.02"},
    )
    assert trendline.status_code == 201
    listed = await journal_client.get("/chart-structures?symbol=BONKUSDT")
    assert [row["id"] for row in listed.json()] == [trendline.json()["id"]]
    updated = await journal_client.patch(f"/chart-structures/{trendline.json()['id']}", json={"anchor_two_price": "0.03", "active": False})
    assert updated.status_code == 200
    assert updated.json()["anchor_two_price"] == 0.03
    zone = await journal_client.post("/chart-structures", json={"symbol": "BONKUSDT", "structure_type": "HORIZONTAL_ZONE", "lower_price": "0.01", "upper_price": "0.02"})
    changed = await journal_client.patch(f"/chart-structures/{zone.json()['id']}", json={"label": "range", "active": False})
    assert changed.json()["label"] == "range"
    assert (await journal_client.delete(f"/chart-structures/{zone.json()['id']}")).status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"symbol": "BTCUSDT", "structure_type": "HORIZONTAL_ZONE", "lower_price": 0, "upper_price": 0},
        {"symbol": "BTCUSDT", "structure_type": "TRENDLINE", "anchor_one_time": 1000, "anchor_one_price": 100, "anchor_two_time": 1000, "anchor_two_price": 110},
        {"symbol": " ", "structure_type": "HORIZONTAL_ZONE", "lower_price": 100, "upper_price": 100},
        {"symbol": "BTCUSDT", "structure_type": "HORIZONTAL_ZONE", "lower_price": "Infinity", "upper_price": "Infinity"},
    ],
)
async def test_malformed_chart_structures_do_not_persist(journal_client, body):
    response = await journal_client.post("/chart-structures", json=body)

    assert response.status_code == 422
    assert (await journal_client.get("/chart-structures")).json() == []
