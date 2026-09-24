from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime

from trading_copilot.domain.scanner import ScannerDataStatus
from trading_copilot.domain.trigger import LowerTimeframeBar, LowerTimeframeTriggerSnapshot
from trading_copilot.services.bybit_public import BybitPublicClient
from trading_copilot.services.live_market import LiveMarketStore

FIVE_MINUTES_MS = 5 * 60 * 1000
FIFTEEN_MINUTES_MS = 15 * 60 * 1000


def convert_trigger_klines(
    rows: Iterable[list[str]], *, interval_ms: int
) -> list[LowerTimeframeBar]:
    by_start: dict[int, LowerTimeframeBar] = {}
    for row in rows:
        if len(row) < 6:
            raise ValueError("malformed trigger kline row")
        try:
            start_ms = int(row[0])
            converted = LowerTimeframeBar(
                start_ms=start_ms,
                end_ms=start_ms + interval_ms,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed trigger kline row") from exc
        existing = by_start.get(start_ms)
        if existing is not None and existing != converted:
            raise ValueError(f"conflicting trigger kline interval: {start_ms}")
        by_start[start_ms] = converted
    bars = sorted(by_start.values(), key=lambda bar: (bar.start_ms, bar.end_ms))
    if len({bar.end_ms for bar in bars}) != len(bars):
        raise ValueError("conflicting trigger kline end timestamp")
    return bars


async def build_trigger_snapshot(
    symbol: str,
    *,
    evaluated_at: datetime | None = None,
    client: BybitPublicClient | None = None,
    live_market: LiveMarketStore | None = None,
) -> LowerTimeframeTriggerSnapshot:
    evaluated_at = evaluated_at or datetime.now(UTC)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must not be empty")
    client = client or BybitPublicClient()
    rows_5m, rows_15m = await asyncio.gather(
        client.klines(normalized_symbol, "5", 200),
        client.klines(normalized_symbol, "15", 200),
    )
    bars_5m = convert_trigger_klines(rows_5m, interval_ms=FIVE_MINUTES_MS)
    bars_15m = convert_trigger_klines(rows_15m, interval_ms=FIFTEEN_MINUTES_MS)

    reaction_state: str | None = None
    diagnostics: list[str] = []
    if live_market is not None and live_market.has_data(normalized_symbol):
        try:
            reaction = live_market.reaction_state(normalized_symbol, 60_000).get("reaction")
            if reaction is not None:
                reaction_state = reaction.get("state")
        except Exception:  # noqa: BLE001 - optional reaction context degrades safely
            diagnostics.append("REACTION_UNAVAILABLE")
    if reaction_state is None and "REACTION_UNAVAILABLE" not in diagnostics:
        diagnostics.append("REACTION_NOT_AVAILABLE")
    return LowerTimeframeTriggerSnapshot(
        symbol=normalized_symbol,
        evaluated_at=evaluated_at,
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        reaction_state=reaction_state,
        data_status=(
            ScannerDataStatus.CONFIRMED.value
            if reaction_state is not None
            else ScannerDataStatus.PARTIAL.value
        ),
        diagnostics=diagnostics,
    )
