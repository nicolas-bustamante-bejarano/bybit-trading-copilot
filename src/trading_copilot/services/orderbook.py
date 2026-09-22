from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)
TEN_THOUSAND = Decimal(10000)


@dataclass(slots=True)
class BookChangeStats:
    timestamp_ms: int
    bid_added_qty: Decimal = ZERO
    bid_removed_qty: Decimal = ZERO
    ask_added_qty: Decimal = ZERO
    ask_removed_qty: Decimal = ZERO
    bid_replenished_qty: Decimal = ZERO
    ask_replenished_qty: Decimal = ZERO

    def as_dict(self) -> dict[str, float]:
        return {
            "bid_added_qty": float(self.bid_added_qty),
            "bid_removed_qty": float(self.bid_removed_qty),
            "ask_added_qty": float(self.ask_added_qty),
            "ask_removed_qty": float(self.ask_removed_qty),
            "bid_replenished_qty": float(self.bid_replenished_qty),
            "ask_replenished_qty": float(self.ask_replenished_qty),
        }


class LocalOrderBook:
    """In-memory Bybit order book reconstructed from snapshot + delta messages."""

    def __init__(self, replenishment_window_ms: int = 5_000) -> None:
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.update_id: int | None = None
        self.sequence: int | None = None
        self.timestamp_ms: int | None = None
        self.replenishment_window_ms = replenishment_window_ms
        self._recent_bid_removals: dict[Decimal, tuple[Decimal, int]] = {}
        self._recent_ask_removals: dict[Decimal, tuple[Decimal, int]] = {}

    @staticmethod
    def _parse_levels(levels: Iterable[Iterable[str]]) -> dict[Decimal, Decimal]:
        parsed: dict[Decimal, Decimal] = {}
        for price_raw, qty_raw in levels:
            price = Decimal(price_raw)
            qty = Decimal(qty_raw)
            if qty > ZERO:
                parsed[price] = qty
        return parsed

    def apply_snapshot(
        self,
        bids: Iterable[Iterable[str]],
        asks: Iterable[Iterable[str]],
        *,
        update_id: int,
        sequence: int,
        timestamp_ms: int,
    ) -> None:
        self.bids = self._parse_levels(bids)
        self.asks = self._parse_levels(asks)
        self.update_id = update_id
        self.sequence = sequence
        self.timestamp_ms = timestamp_ms
        self._recent_bid_removals.clear()
        self._recent_ask_removals.clear()

    def apply_delta(
        self,
        bids: Iterable[Iterable[str]],
        asks: Iterable[Iterable[str]],
        *,
        update_id: int,
        sequence: int,
        timestamp_ms: int,
    ) -> BookChangeStats:
        if self.update_id is None:
            raise RuntimeError("order book requires a snapshot before deltas")
        if update_id == 1:
            raise RuntimeError("Bybit reset signal received; a fresh snapshot is required")
        if self.sequence is not None and sequence < self.sequence:
            raise RuntimeError("out-of-order order-book sequence")
        if update_id <= self.update_id:
            raise RuntimeError("stale order-book update")

        stats = BookChangeStats(timestamp_ms=timestamp_ms)
        self._expire_replenishment_candidates(timestamp_ms)
        self._apply_side(
            self.bids,
            bids,
            self._recent_bid_removals,
            timestamp_ms,
            stats,
            side="bid",
        )
        self._apply_side(
            self.asks,
            asks,
            self._recent_ask_removals,
            timestamp_ms,
            stats,
            side="ask",
        )
        self.update_id = update_id
        self.sequence = sequence
        self.timestamp_ms = timestamp_ms
        return stats

    def _expire_replenishment_candidates(self, now_ms: int) -> None:
        cutoff = now_ms - self.replenishment_window_ms
        for removals in (self._recent_bid_removals, self._recent_ask_removals):
            for price, (_, ts) in list(removals.items()):
                if ts < cutoff:
                    removals.pop(price, None)

    def _apply_side(
        self,
        book: dict[Decimal, Decimal],
        updates: Iterable[Iterable[str]],
        recent_removals: dict[Decimal, tuple[Decimal, int]],
        timestamp_ms: int,
        stats: BookChangeStats,
        *,
        side: str,
    ) -> None:
        for price_raw, qty_raw in updates:
            price = Decimal(price_raw)
            new_qty = Decimal(qty_raw)
            old_qty = book.get(price, ZERO)
            delta = new_qty - old_qty

            if new_qty == ZERO:
                book.pop(price, None)
            else:
                book[price] = new_qty

            if delta > ZERO:
                replenished = ZERO
                previous_removal = recent_removals.get(price)
                if previous_removal is not None:
                    removed_qty, removed_at = previous_removal
                    if timestamp_ms - removed_at <= self.replenishment_window_ms:
                        replenished = min(delta, removed_qty)
                        remaining = removed_qty - replenished
                        if remaining > ZERO:
                            recent_removals[price] = (remaining, removed_at)
                        else:
                            recent_removals.pop(price, None)
                if side == "bid":
                    stats.bid_added_qty += delta
                    stats.bid_replenished_qty += replenished
                else:
                    stats.ask_added_qty += delta
                    stats.ask_replenished_qty += replenished
            elif delta < ZERO:
                removed = -delta
                recent_removals[price] = (removed, timestamp_ms)
                if side == "bid":
                    stats.bid_removed_qty += removed
                else:
                    stats.ask_removed_qty += removed

    @property
    def best_bid(self) -> Decimal | None:
        return max(self.bids, default=None)

    @property
    def best_ask(self) -> Decimal | None:
        return min(self.asks, default=None)

    @property
    def mid_price(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / Decimal(2)

    @property
    def spread_bps(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        mid = self.mid_price
        if bid is None or ask is None or mid in (None, ZERO):
            return None
        return (ask - bid) / mid * TEN_THOUSAND

    def depth_within_bps(self, bps: int) -> tuple[Decimal, Decimal]:
        mid = self.mid_price
        if mid is None:
            return ZERO, ZERO
        band = Decimal(bps) / TEN_THOUSAND
        bid_floor = mid * (Decimal(1) - band)
        ask_ceiling = mid * (Decimal(1) + band)
        bid_qty = sum(qty for price, qty in self.bids.items() if price >= bid_floor)
        ask_qty = sum(qty for price, qty in self.asks.items() if price <= ask_ceiling)
        return bid_qty, ask_qty

    def depth_notional_within_bps(self, bps: int) -> tuple[Decimal, Decimal]:
        mid = self.mid_price
        if mid is None:
            return ZERO, ZERO
        band = Decimal(bps) / TEN_THOUSAND
        bid_floor = mid * (Decimal(1) - band)
        ask_ceiling = mid * (Decimal(1) + band)
        bid_notional = sum(price * qty for price, qty in self.bids.items() if price >= bid_floor)
        ask_notional = sum(price * qty for price, qty in self.asks.items() if price <= ask_ceiling)
        return bid_notional, ask_notional

    def imbalance(self, bps: int) -> Decimal:
        bid_notional, ask_notional = self.depth_notional_within_bps(bps)
        total = bid_notional + ask_notional
        if total == ZERO:
            return ZERO
        return (bid_notional - ask_notional) / total

    def top_levels(self, depth: int = 10) -> dict[str, list[list[float]]]:
        bids = sorted(self.bids.items(), reverse=True)[:depth]
        asks = sorted(self.asks.items())[:depth]
        return {
            "bids": [[float(price), float(qty)] for price, qty in bids],
            "asks": [[float(price), float(qty)] for price, qty in asks],
        }


class RollingBookChanges:
    def __init__(self, window_ms: int = 60_000) -> None:
        self.window_ms = window_ms
        self._events: deque[BookChangeStats] = deque()

    def add(self, event: BookChangeStats) -> None:
        self._events.append(event)
        self._prune(event.timestamp_ms)

    def snapshot(self, now_ms: int) -> dict[str, float]:
        self._prune(now_ms)
        totals = BookChangeStats(timestamp_ms=now_ms)
        for event in self._events:
            totals.bid_added_qty += event.bid_added_qty
            totals.bid_removed_qty += event.bid_removed_qty
            totals.ask_added_qty += event.ask_added_qty
            totals.ask_removed_qty += event.ask_removed_qty
            totals.bid_replenished_qty += event.bid_replenished_qty
            totals.ask_replenished_qty += event.ask_replenished_qty
        payload = totals.as_dict()
        payload["window_ms"] = self.window_ms
        return payload

    def _prune(self, now_ms: int) -> None:
        cutoff = now_ms - self.window_ms
        while self._events and self._events[0].timestamp_ms < cutoff:
            self._events.popleft()
