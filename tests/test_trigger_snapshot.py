from datetime import UTC, datetime

import pytest

from trading_copilot.services.trigger_snapshot import (
    FIFTEEN_MINUTES_MS,
    FIVE_MINUTES_MS,
    build_trigger_snapshot,
    convert_trigger_klines,
)

NOW = datetime(2026, 1, 1, 10, 7, tzinfo=UTC)
START_MS = int(datetime(2026, 1, 1, 10, 5, tzinfo=UTC).timestamp() * 1000)


def row(start=START_MS, open_=100, high=102, low=99, close=101, volume=12):
    return [str(start), str(open_), str(high), str(low), str(close), str(volume), "ignored"]


class FakeClient:
    def __init__(self, rows_5m=None, rows_15m=None, failure=None):
        self.rows_5m = rows_5m if rows_5m is not None else [row()]
        self.rows_15m = rows_15m if rows_15m is not None else [row()]
        self.failure = failure
        self.calls = []

    async def klines(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        if self.failure == interval:
            raise RuntimeError(f"{interval}m unavailable")
        return self.rows_5m if interval == "5" else self.rows_15m


@pytest.mark.asyncio
async def test_snapshot_fetches_only_one_5m_and_one_15m_history():
    client = FakeClient()

    snapshot = await build_trigger_snapshot(
        "btcusdt", evaluated_at=NOW, client=client
    )

    assert snapshot.symbol == "BTCUSDT"
    assert sorted(client.calls) == [("BTCUSDT", "15", 200), ("BTCUSDT", "5", 200)]
    assert len(client.calls) == 2


def test_kline_conversion_preserves_ohlcv_and_exchange_end_times():
    five = convert_trigger_klines([row()], interval_ms=FIVE_MINUTES_MS)[0]
    fifteen = convert_trigger_klines([row()], interval_ms=FIFTEEN_MINUTES_MS)[0]

    assert (five.open, five.high, five.low, five.close, five.volume) == (
        100,
        102,
        99,
        101,
        12,
    )
    assert five.end_ms == START_MS + FIVE_MINUTES_MS
    assert fifteen.end_ms == START_MS + FIFTEEN_MINUTES_MS


@pytest.mark.asyncio
async def test_partial_rest_rows_keep_future_exchange_boundary():
    snapshot = await build_trigger_snapshot("BTCUSDT", evaluated_at=NOW, client=FakeClient())

    assert snapshot.bars_5m[0].end_ms > int(NOW.timestamp() * 1000)
    assert snapshot.bars_15m[0].end_ms > int(NOW.timestamp() * 1000)


def test_kline_conversion_orders_oldest_to_newest():
    older = row(START_MS - FIVE_MINUTES_MS, close=100)
    newer = row(START_MS, close=101)

    bars = convert_trigger_klines([newer, older], interval_ms=FIVE_MINUTES_MS)

    assert [bar.start_ms for bar in bars] == [START_MS - FIVE_MINUTES_MS, START_MS]


def test_exact_duplicate_rows_collapse():
    duplicate = row()

    bars = convert_trigger_klines([duplicate, list(duplicate)], interval_ms=FIVE_MINUTES_MS)

    assert len(bars) == 1


def test_conflicting_duplicate_interval_fails():
    with pytest.raises(ValueError, match="conflicting trigger kline interval"):
        convert_trigger_klines(
            [row(close=100), row(close=101)], interval_ms=FIVE_MINUTES_MS
        )


@pytest.mark.parametrize(
    "malformed",
    [
        ["1", "2"],
        row(open_="bad"),
        row(low=103, high=102),
        row(volume=-1),
    ],
)
def test_malformed_kline_fails(malformed):
    with pytest.raises(ValueError, match="malformed trigger kline row"):
        convert_trigger_klines([malformed], interval_ms=FIVE_MINUTES_MS)


class LiveMarket:
    def __init__(self, *, failure=False):
        self.failure = failure

    def has_data(self, symbol):
        return True

    def reaction_state(self, symbol, interval_ms):
        if self.failure:
            raise RuntimeError("reaction unavailable")
        return {"reaction": {"state": "sell_absorption"}}


@pytest.mark.asyncio
async def test_optional_reaction_uses_existing_live_market_classifier():
    snapshot = await build_trigger_snapshot(
        "BTCUSDT", evaluated_at=NOW, client=FakeClient(), live_market=LiveMarket()
    )

    assert snapshot.reaction_state == "sell_absorption"
    assert snapshot.data_status == "CONFIRMED"
    assert snapshot.diagnostics == []


@pytest.mark.asyncio
async def test_reaction_failure_degrades_without_losing_price_bars():
    snapshot = await build_trigger_snapshot(
        "BTCUSDT",
        evaluated_at=NOW,
        client=FakeClient(),
        live_market=LiveMarket(failure=True),
    )

    assert snapshot.reaction_state is None
    assert snapshot.data_status == "PARTIAL"
    assert snapshot.diagnostics == ["REACTION_UNAVAILABLE"]
    assert snapshot.bars_5m and snapshot.bars_15m


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", ["5", "15"])
async def test_critical_kline_failure_does_not_fabricate_snapshot(interval):
    with pytest.raises(RuntimeError, match="unavailable"):
        await build_trigger_snapshot(
            "BTCUSDT", evaluated_at=NOW, client=FakeClient(failure=interval)
        )


@pytest.mark.asyncio
async def test_malformed_critical_kline_fails_snapshot():
    with pytest.raises(ValueError, match="malformed trigger kline"):
        await build_trigger_snapshot(
            "BTCUSDT",
            evaluated_at=NOW,
            client=FakeClient(rows_5m=[["bad"]]),
        )
