import httpx

from trading_copilot.config import settings


class BybitPublicClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.bybit_base_url

    async def _get(self, path: str, params: dict) -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error: {payload.get('retMsg')}")
        return payload["result"]

    async def ticker(self, symbol: str) -> dict:
        result = await self._get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        return result["list"][0]

    async def klines(self, symbol: str, interval: str = "60", limit: int = 200) -> list[list[str]]:
        result = await self._get(
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        )
        return list(reversed(result["list"]))

    async def orderbook(self, symbol: str, limit: int = 50) -> dict:
        return await self._get(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": limit},
        )

    async def funding_history(self, symbol: str, limit: int = 1) -> list[dict]:
        result = await self._get(
            "/v5/market/funding/history",
            {"category": "linear", "symbol": symbol, "limit": limit},
        )
        return result["list"]

    async def open_interest(
        self,
        symbol: str,
        interval: str = "5min",
        limit: int = 50,
    ) -> list[dict]:
        result = await self._get(
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": interval,
                "limit": limit,
            },
        )
        return result["list"]
