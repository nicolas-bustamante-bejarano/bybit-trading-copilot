from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TradePrint:
    timestamp_ms: int
    side: str
    price: float
    size: float


class RollingTradeFlow:
    def __init__(self, window_ms: int = 60_000) -> None:
        self.window_ms = window_ms
        self._trades: deque[TradePrint] = deque()

    def add(self, trade: TradePrint) -> None:
        side = trade.side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"unsupported taker side: {trade.side}")
        self._trades.append(trade)
        self._prune(trade.timestamp_ms)

    def snapshot(self, now_ms: int) -> dict[str, float | int]:
        self._prune(now_ms)
        buy_qty = 0.0
        sell_qty = 0.0
        buy_notional = 0.0
        sell_notional = 0.0
        for trade in self._trades:
            notional = trade.price * trade.size
            if trade.side.lower() == "buy":
                buy_qty += trade.size
                buy_notional += notional
            else:
                sell_qty += trade.size
                sell_notional += notional
        total_notional = buy_notional + sell_notional
        return {
            "window_ms": self.window_ms,
            "trade_count": len(self._trades),
            "buy_qty": buy_qty,
            "sell_qty": sell_qty,
            "delta_qty": buy_qty - sell_qty,
            "buy_notional": buy_notional,
            "sell_notional": sell_notional,
            "delta_notional": buy_notional - sell_notional,
            "buy_notional_share": (buy_notional / total_notional) if total_notional else 0.5,
        }

    def _prune(self, now_ms: int) -> None:
        cutoff = now_ms - self.window_ms
        while self._trades and self._trades[0].timestamp_ms < cutoff:
            self._trades.popleft()
