from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from trading_copilot.domain.models import Regime
from trading_copilot.domain.scanner import ScannerDataStatus
from trading_copilot.services.bybit_public import BybitPublicClient
from trading_copilot.services.indicators import ema, stoch_rsi
from trading_copilot.services.live_market import LiveMarketStore
from trading_copilot.services.regime import classify_regime

HOUR_MS = 60 * 60 * 1000
FOUR_HOURS_MS = 4 * HOUR_MS


@dataclass(frozen=True)
class ScannerTimeframeSnapshot:
    regime: Regime
    close: float
    ema12: float
    ema21: float
    stoch_k: float | None = None
    stoch_d: float | None = None
    prev_stoch_k: float | None = None
    prev_stoch_d: float | None = None


@dataclass(frozen=True)
class ScannerSymbolSnapshot:
    symbol: str
    current_price: float
    evaluated_at: datetime
    one_hour: ScannerTimeframeSnapshot
    four_hour: ScannerTimeframeSnapshot
    completed_1h_closes: tuple[float, ...]
    reaction_state: str | None
    data_status: ScannerDataStatus
    metadata: dict[str, Any] = field(default_factory=dict)


def completed_kline_closes(
    rows: list[list[str]], *, interval_ms: int, evaluated_at: datetime
) -> list[float]:
    """Return closes whose exchange-aligned interval ended by evaluation time."""
    evaluated_ms = int(evaluated_at.timestamp() * 1000)
    completed: list[tuple[int, float]] = []
    for row in rows:
        if len(row) < 5:
            raise ValueError("malformed kline row")
        start_ms = int(row[0])
        close = float(row[4])
        if not isfinite(close) or close <= 0:
            raise ValueError("kline close must be positive")
        if start_ms + interval_ms <= evaluated_ms:
            completed.append((start_ms, close))
    completed.sort(key=lambda item: item[0])
    return [close for _, close in completed]


def _timeframe_features(closes: list[float], *, include_stoch: bool) -> ScannerTimeframeSnapshot:
    if len(closes) < 2:
        raise ValueError("at least two completed closes are required")
    ema12 = ema(closes, 12)
    ema21 = ema(closes, 21)
    regime = classify_regime(ema12[-1], ema21[-1], ema12[-2], ema21[-2])
    k_values, d_values = stoch_rsi(closes) if include_stoch else ([], [])
    return ScannerTimeframeSnapshot(
        regime=regime,
        close=closes[-1],
        ema12=ema12[-1],
        ema21=ema21[-1],
        stoch_k=k_values[-1] if k_values else None,
        stoch_d=d_values[-1] if d_values else None,
        prev_stoch_k=k_values[-2] if len(k_values) >= 2 else None,
        prev_stoch_d=d_values[-2] if len(d_values) >= 2 else None,
    )


async def build_scanner_snapshot(
    symbol: str,
    *,
    evaluated_at: datetime | None = None,
    client: BybitPublicClient | None = None,
    live_market: LiveMarketStore | None = None,
) -> ScannerSymbolSnapshot:
    evaluated_at = evaluated_at or datetime.now(UTC)
    normalized_symbol = symbol.upper()
    client = client or BybitPublicClient()
    ticker, rows_1h, rows_4h = await asyncio.gather(
        client.ticker(normalized_symbol),
        client.klines(normalized_symbol, "60", 200),
        client.klines(normalized_symbol, "240", 200),
    )
    try:
        current_price = float(ticker["lastPrice"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ticker lastPrice is missing or malformed") from exc
    if not isfinite(current_price) or current_price <= 0:
        raise ValueError("ticker lastPrice must be positive")

    closes_1h = completed_kline_closes(
        rows_1h, interval_ms=HOUR_MS, evaluated_at=evaluated_at
    )
    closes_4h = completed_kline_closes(
        rows_4h, interval_ms=FOUR_HOURS_MS, evaluated_at=evaluated_at
    )
    one_hour = _timeframe_features(closes_1h, include_stoch=True)
    four_hour = _timeframe_features(closes_4h, include_stoch=False)

    reaction_state = None
    live_payload: dict[str, Any] = {}
    if live_market is not None and live_market.has_data(normalized_symbol):
        try:
            live_payload = live_market.state(normalized_symbol)
            reaction = live_market.reaction_state(normalized_symbol, 60_000).get("reaction")
            if reaction is not None:
                reaction_state = reaction.get("state")
        except Exception as exc:  # noqa: BLE001 - optional live context degrades safely
            live_payload = {"error": str(exc)}

    return ScannerSymbolSnapshot(
        symbol=normalized_symbol,
        current_price=current_price,
        evaluated_at=evaluated_at,
        one_hour=one_hour,
        four_hour=four_hour,
        completed_1h_closes=tuple(closes_1h),
        reaction_state=reaction_state,
        data_status=(
            ScannerDataStatus.CONFIRMED
            if reaction_state is not None
            else ScannerDataStatus.PARTIAL
        ),
        metadata={
            "funding_rate": _optional_float(ticker.get("fundingRate")),
            "open_interest": _optional_float(ticker.get("openInterest")),
            "live": live_payload,
        },
    )


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
