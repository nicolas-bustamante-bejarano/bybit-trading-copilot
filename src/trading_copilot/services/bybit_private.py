from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx


class BybitReadOnlyClient:
    """Minimal authenticated Bybit V5 client for read-only account data.

    This client intentionally exposes GET-only account endpoints and contains no
    order placement, amendment, cancellation, transfer, or withdrawal methods.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.bybit.com",
        recv_window: int = 5_000,
        timeout: float = 10.0,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Bybit API key and secret are required")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.timeout = timeout

    @staticmethod
    def canonical_query(params: dict[str, Any] | None) -> str:
        if not params:
            return ""
        cleaned = {key: value for key, value in params.items() if value is not None}
        return urlencode(sorted(cleaned.items()), doseq=True)

    def signature(self, *, timestamp_ms: int, query_string: str) -> str:
        payload = f"{timestamp_ms}{self.api_key}{self.recv_window}{query_string}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        timestamp_ms = int(time.time() * 1000)
        query_string = self.canonical_query(params)
        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": str(timestamp_ms),
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "X-BAPI-SIGN": self.signature(timestamp_ms=timestamp_ms, query_string=query_string),
        }
        url = f"{self.base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()

        if payload.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit API error {payload.get('retCode')}: {payload.get('retMsg', 'unknown error')}"
            )
        return payload

    async def positions(self, settle_coin: str = "USDT") -> list[dict[str, Any]]:
        payload = await self._get(
            "/v5/position/list",
            {"category": "linear", "settleCoin": settle_coin},
        )
        return payload["result"]["list"]

    async def executions(self, limit: int = 100) -> list[dict[str, Any]]:
        payload = await self._get(
            "/v5/execution/list",
            {"category": "linear", "limit": limit},
        )
        return payload["result"]["list"]

    async def open_orders(self, settle_coin: str = "USDT") -> list[dict[str, Any]]:
        payload = await self._get(
            "/v5/order/realtime",
            {"category": "linear", "settleCoin": settle_coin, "openOnly": 0},
        )
        return payload["result"]["list"]

    async def wallet_balance(self) -> list[dict[str, Any]]:
        payload = await self._get(
            "/v5/account/wallet-balance",
            {"accountType": "UNIFIED"},
        )
        return payload["result"]["list"]

    async def account_snapshot(self) -> dict[str, Any]:
        positions, executions, orders, wallet = await __import__("asyncio").gather(
            self.positions(),
            self.executions(),
            self.open_orders(),
            self.wallet_balance(),
        )
        return {
            "positions": positions,
            "executions": executions,
            "open_orders": orders,
            "wallet": wallet,
        }
