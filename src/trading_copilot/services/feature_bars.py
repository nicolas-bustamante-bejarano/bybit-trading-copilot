from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

from trading_copilot.services.orderbook import BookChangeStats, LocalOrderBook


@dataclass(slots=True)
class FeatureBar:
    symbol: str
    interval_ms: int
    start_ms: int
    end_ms: int
    mid_open: float | None = None
    mid_high: float | None = None
    mid_low: float | None = None
    mid_close: float | None = None
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    bid_added_qty: float = 0.0
    bid_removed_qty: float = 0.0
    ask_added_qty: float = 0.0
    ask_removed_qty: float = 0.0
    bid_replenished_qty: float = 0.0
    ask_replenished_qty: float = 0.0
    imbalance_10bps: float | None = None
    imbalance_25bps: float | None = None
    imbalance_50bps: float | None = None
    open_interest_open: float | None = None
    open_interest_close: float | None = None
    funding_rate: float | None = None

    @property
    def delta_notional(self) -> float:
        return self.buy_notional - self.sell_notional

    @property
    def total_notional(self) -> float:
        return self.buy_notional + self.sell_notional

    @property
    def flow_imbalance(self) -> float:
        if self.total_notional == 0:
            return 0.0
        return self.delta_notional / self.total_notional

    @property
    def mid_change_bps(self) -> float | None:
        if self.mid_open in (None, 0) or self.mid_close is None:
            return None
        return (self.mid_close - self.mid_open) / self.mid_open * 10_000

    @property
    def open_interest_delta(self) -> float | None:
        if self.open_interest_open is None or self.open_interest_close is None:
            return None
        return self.open_interest_close - self.open_interest_open

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "delta_notional": self.delta_notional,
                "total_notional": self.total_notional,
                "flow_imbalance": self.flow_imbalance,
                "mid_change_bps": self.mid_change_bps,
                "open_interest_delta": self.open_interest_delta,
            }
        )
        return payload


class FeatureBarAggregator:
    """Aggregate event-level microstructure data into fixed time buckets."""

    def __init__(
        self,
        symbol: str,
        interval_ms: int,
        *,
        history_size: int = 600,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self.symbol = symbol.upper()
        self.interval_ms = interval_ms
        self.history: deque[FeatureBar] = deque(maxlen=history_size)
        self.current: FeatureBar | None = None

    def _bucket_start(self, timestamp_ms: int) -> int:
        return timestamp_ms - (timestamp_ms % self.interval_ms)

    def _ensure_bar(self, timestamp_ms: int) -> FeatureBar:
        start = self._bucket_start(timestamp_ms)
        if self.current is None:
            self.current = FeatureBar(
                symbol=self.symbol,
                interval_ms=self.interval_ms,
                start_ms=start,
                end_ms=start + self.interval_ms,
            )
        elif start > self.current.start_ms:
            self.history.append(self.current)
            self.current = FeatureBar(
                symbol=self.symbol,
                interval_ms=self.interval_ms,
                start_ms=start,
                end_ms=start + self.interval_ms,
            )
        elif start < self.current.start_ms:
            raise RuntimeError("out-of-order feature-bar event")
        return self.current

    def on_book(
        self,
        timestamp_ms: int,
        book: LocalOrderBook,
        changes: BookChangeStats | None = None,
    ) -> None:
        bar = self._ensure_bar(timestamp_ms)
        if book.mid_price is not None:
            mid = float(book.mid_price)
            if bar.mid_open is None:
                bar.mid_open = mid
                bar.mid_high = mid
                bar.mid_low = mid
            bar.mid_close = mid
            bar.mid_high = max(bar.mid_high or mid, mid)
            bar.mid_low = min(bar.mid_low or mid, mid)
            bar.imbalance_10bps = float(book.imbalance(10))
            bar.imbalance_25bps = float(book.imbalance(25))
            bar.imbalance_50bps = float(book.imbalance(50))

        if changes is not None:
            bar.bid_added_qty += float(changes.bid_added_qty)
            bar.bid_removed_qty += float(changes.bid_removed_qty)
            bar.ask_added_qty += float(changes.ask_added_qty)
            bar.ask_removed_qty += float(changes.ask_removed_qty)
            bar.bid_replenished_qty += float(changes.bid_replenished_qty)
            bar.ask_replenished_qty += float(changes.ask_replenished_qty)

    def on_trade(self, timestamp_ms: int, side: str, price: float, size: float) -> None:
        bar = self._ensure_bar(timestamp_ms)
        notional = price * size
        normalized_side = side.lower()
        if normalized_side == "buy":
            bar.buy_notional += notional
        elif normalized_side == "sell":
            bar.sell_notional += notional
        else:
            raise ValueError(f"unsupported taker side: {side}")

    def on_ticker(
        self,
        timestamp_ms: int,
        *,
        open_interest: float | None,
        funding_rate: float | None,
    ) -> None:
        bar = self._ensure_bar(timestamp_ms)
        if open_interest is not None:
            if bar.open_interest_open is None:
                bar.open_interest_open = open_interest
            bar.open_interest_close = open_interest
        if funding_rate is not None:
            bar.funding_rate = funding_rate

    def latest_completed(self) -> FeatureBar | None:
        return self.history[-1] if self.history else None

    def current_snapshot(self) -> FeatureBar | None:
        return self.current


class MultiIntervalFeatureBars:
    def __init__(self, intervals_ms: tuple[int, ...] = (1_000, 60_000)) -> None:
        self.intervals_ms = intervals_ms
        self._aggregators: dict[tuple[str, int], FeatureBarAggregator] = {}

    def _for(self, symbol: str, interval_ms: int) -> FeatureBarAggregator:
        key = (symbol.upper(), interval_ms)
        if key not in self._aggregators:
            self._aggregators[key] = FeatureBarAggregator(symbol, interval_ms)
        return self._aggregators[key]

    def on_book(
        self,
        symbol: str,
        timestamp_ms: int,
        book: LocalOrderBook,
        changes: BookChangeStats | None = None,
    ) -> None:
        for interval_ms in self.intervals_ms:
            self._for(symbol, interval_ms).on_book(timestamp_ms, book, changes)

    def on_trade(
        self,
        symbol: str,
        timestamp_ms: int,
        side: str,
        price: float,
        size: float,
    ) -> None:
        for interval_ms in self.intervals_ms:
            self._for(symbol, interval_ms).on_trade(timestamp_ms, side, price, size)

    def on_ticker(
        self,
        symbol: str,
        timestamp_ms: int,
        *,
        open_interest: float | None,
        funding_rate: float | None,
    ) -> None:
        for interval_ms in self.intervals_ms:
            self._for(symbol, interval_ms).on_ticker(
                timestamp_ms,
                open_interest=open_interest,
                funding_rate=funding_rate,
            )

    def snapshot(self, symbol: str, interval_ms: int) -> dict[str, Any]:
        aggregator = self._for(symbol, interval_ms)
        completed = aggregator.latest_completed()
        current = aggregator.current_snapshot()
        return {
            "interval_ms": interval_ms,
            "latest_completed": completed.as_dict() if completed else None,
            "current": current.as_dict() if current else None,
        }
