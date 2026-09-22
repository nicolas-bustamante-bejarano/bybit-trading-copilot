from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from trading_copilot.services.orderbook import LocalOrderBook, RollingBookChanges
from trading_copilot.services.trade_flow import RollingTradeFlow, TradePrint


class LiveMarketStore:
    """Normalized in-memory state built from Bybit public WebSocket messages."""

    def __init__(self) -> None:
        self.books: dict[str, LocalOrderBook] = {}
        self.book_changes: dict[str, RollingBookChanges] = defaultdict(RollingBookChanges)
        self.trade_flows: dict[str, RollingTradeFlow] = defaultdict(RollingTradeFlow)
        self.tickers: dict[str, dict[str, Any]] = {}

    def handle(self, message: dict[str, Any]) -> None:
        topic = message.get("topic", "")
        if topic.startswith("orderbook."):
            self._handle_orderbook(message)
        elif topic.startswith("publicTrade."):
            self._handle_trades(message)
        elif topic.startswith("tickers."):
            self._handle_ticker(message)

    def _handle_orderbook(self, message: dict[str, Any]) -> None:
        data = message["data"]
        symbol = data["s"].upper()
        book = self.books.setdefault(symbol, LocalOrderBook())
        timestamp_ms = int(message.get("ts") or time.time() * 1000)
        update_id = int(data["u"])
        sequence = int(data["seq"])
        message_type = message.get("type")

        if message_type == "snapshot" or update_id == 1:
            book.apply_snapshot(
                data.get("b", []),
                data.get("a", []),
                update_id=update_id,
                sequence=sequence,
                timestamp_ms=timestamp_ms,
            )
            return

        event = book.apply_delta(
            data.get("b", []),
            data.get("a", []),
            update_id=update_id,
            sequence=sequence,
            timestamp_ms=timestamp_ms,
        )
        self.book_changes[symbol].add(event)

    def _handle_trades(self, message: dict[str, Any]) -> None:
        for raw in message.get("data", []):
            symbol = raw["s"].upper()
            self.trade_flows[symbol].add(
                TradePrint(
                    timestamp_ms=int(raw["T"]),
                    side=raw["S"],
                    price=float(raw["p"]),
                    size=float(raw["v"]),
                )
            )

    def _handle_ticker(self, message: dict[str, Any]) -> None:
        data = message.get("data") or {}
        symbol = (data.get("symbol") or message.get("topic", "").split(".")[-1]).upper()
        current = self.tickers.setdefault(symbol, {})
        current.update(data)
        current["updated_at_ms"] = int(message.get("ts") or time.time() * 1000)

    def has_data(self, symbol: str) -> bool:
        symbol = symbol.upper()
        return symbol in self.books or symbol in self.tickers or symbol in self.trade_flows

    def active_symbols(self) -> list[str]:
        return sorted(set(self.books) | set(self.tickers) | set(self.trade_flows))

    def state(self, symbol: str, now_ms: int | None = None) -> dict[str, Any]:
        symbol = symbol.upper()
        now_ms = now_ms or int(time.time() * 1000)
        book = self.books.get(symbol)
        ticker = self.tickers.get(symbol, {})

        orderbook_payload: dict[str, Any] | None = None
        if book is not None and book.mid_price is not None:
            depth: dict[str, Any] = {}
            for bps in (10, 25, 50):
                bid_qty, ask_qty = book.depth_within_bps(bps)
                bid_notional, ask_notional = book.depth_notional_within_bps(bps)
                depth[str(bps)] = {
                    "bid_qty": float(bid_qty),
                    "ask_qty": float(ask_qty),
                    "bid_notional": float(bid_notional),
                    "ask_notional": float(ask_notional),
                    "imbalance": float(book.imbalance(bps)),
                }
            orderbook_payload = {
                "best_bid": float(book.best_bid) if book.best_bid is not None else None,
                "best_ask": float(book.best_ask) if book.best_ask is not None else None,
                "mid_price": float(book.mid_price),
                "spread_bps": float(book.spread_bps) if book.spread_bps is not None else None,
                "depth": depth,
                "change_flow_60s": self.book_changes[symbol].snapshot(now_ms),
                "top_levels": book.top_levels(10),
                "update_id": book.update_id,
                "sequence": book.sequence,
            }

        return {
            "source": "bybit",
            "category": "linear",
            "symbol": symbol,
            "timestamp_ms": now_ms,
            "last_price": _float_or_none(ticker.get("lastPrice")),
            "mark_price": _float_or_none(ticker.get("markPrice")),
            "index_price": _float_or_none(ticker.get("indexPrice")),
            "open_interest": _float_or_none(ticker.get("openInterest")),
            "funding_rate": _float_or_none(ticker.get("fundingRate")),
            "orderbook": orderbook_payload,
            "trade_flow_60s": self.trade_flows[symbol].snapshot(now_ms),
        }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
