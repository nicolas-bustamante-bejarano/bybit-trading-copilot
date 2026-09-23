from datetime import UTC, datetime, timedelta

import pytest

from trading_copilot.domain.scanner import ScannerDataStatus
from trading_copilot.services.scanner_snapshot import (
    HOUR_MS,
    build_scanner_snapshot,
    completed_kline_closes,
)

EVALUATED_AT = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)


def kline(start: datetime, close: float) -> list[str]:
    timestamp = int(start.timestamp() * 1000)
    return [str(timestamp), str(close), str(close), str(close), str(close), "1", "1"]


def history(interval_hours: int, count: int = 50) -> list[list[str]]:
    last_start = EVALUATED_AT - timedelta(hours=interval_hours)
    return [
        kline(last_start - timedelta(hours=interval_hours * offset), 100 + count - offset)
        for offset in reversed(range(count))
    ]


class FakeClient:
    def __init__(self, *, ticker=None, one_hour=None, four_hour=None):
        self._ticker = ticker or {
            "lastPrice": "151",
            "fundingRate": "0.0001",
            "openInterest": "1234",
        }
        self._one_hour = one_hour or history(1)
        self._four_hour = four_hour or history(4)

    async def ticker(self, symbol):
        return self._ticker

    async def klines(self, symbol, interval, limit):
        return self._one_hour if interval == "60" else self._four_hour


def test_current_partial_hour_is_excluded_before_boundary():
    evaluated_at = datetime(2026, 1, 1, 10, 45, tzinfo=UTC)
    rows = [
        kline(datetime(2026, 1, 1, 9, 0, tzinfo=UTC), 99),
        kline(datetime(2026, 1, 1, 10, 0, tzinfo=UTC), 100),
    ]

    assert completed_kline_closes(rows, interval_ms=HOUR_MS, evaluated_at=evaluated_at) == [
        99
    ]


def test_hour_is_completed_at_exact_next_boundary():
    rows = [kline(datetime(2026, 1, 1, 10, 0, tzinfo=UTC), 100)]

    assert completed_kline_closes(
        rows, interval_ms=HOUR_MS, evaluated_at=EVALUATED_AT
    ) == [100]


@pytest.mark.asyncio
async def test_snapshot_normalizes_shared_playbook_inputs_and_stoch_history():
    snapshot = await build_scanner_snapshot(
        "btcusdt", evaluated_at=EVALUATED_AT, client=FakeClient()
    )

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.current_price == 151
    assert snapshot.evaluated_at == EVALUATED_AT
    assert len(snapshot.completed_1h_closes) == 50
    assert snapshot.one_hour.stoch_k is not None
    assert snapshot.one_hour.stoch_d is not None
    assert snapshot.one_hour.prev_stoch_k is not None
    assert snapshot.one_hour.prev_stoch_d is not None
    assert snapshot.metadata["funding_rate"] == 0.0001
    assert snapshot.metadata["open_interest"] == 1234


@pytest.mark.asyncio
async def test_missing_optional_reaction_is_partial_without_fake_signal():
    snapshot = await build_scanner_snapshot(
        "BTCUSDT", evaluated_at=EVALUATED_AT, client=FakeClient()
    )

    assert snapshot.reaction_state is None
    assert snapshot.data_status == ScannerDataStatus.PARTIAL


@pytest.mark.asyncio
async def test_malformed_critical_market_data_fails_snapshot():
    with pytest.raises(ValueError, match="lastPrice"):
        await build_scanner_snapshot(
            "BTCUSDT",
            evaluated_at=EVALUATED_AT,
            client=FakeClient(ticker={"lastPrice": "not-a-price"}),
        )
