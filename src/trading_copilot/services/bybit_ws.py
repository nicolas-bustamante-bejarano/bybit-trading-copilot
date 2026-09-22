from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class BybitLinearStream:
    def __init__(
        self,
        symbols: list[str],
        *,
        url: str = "wss://stream.bybit.com/v5/public/linear",
        orderbook_depth: int = 50,
    ) -> None:
        self.symbols = sorted({symbol.upper() for symbol in symbols})
        self.url = url
        self.orderbook_depth = orderbook_depth
        self._stopping = asyncio.Event()

    @property
    def subscription_args(self) -> list[str]:
        topics: list[str] = []
        for symbol in self.symbols:
            topics.extend(
                [
                    f"orderbook.{self.orderbook_depth}.{symbol}",
                    f"publicTrade.{symbol}",
                    f"tickers.{symbol}",
                ]
            )
        return topics

    async def stop(self) -> None:
        self._stopping.set()

    async def run(self, handler: MessageHandler) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                await self._consume_once(handler)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Bybit public stream disconnected")
                if self._stopping.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _consume_once(self, handler: MessageHandler) -> None:
        async with websockets.connect(
            self.url,
            ping_interval=None,
            close_timeout=5,
            max_queue=4096,
        ) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": self.subscription_args}))
            while not self._stopping.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                except TimeoutError:
                    await ws.send(json.dumps({"op": "ping"}))
                    continue
                message = json.loads(raw)
                if "topic" not in message:
                    continue
                result = handler(message)
                if asyncio.iscoroutine(result):
                    await result
