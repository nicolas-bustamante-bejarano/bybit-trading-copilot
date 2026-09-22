from trading_copilot.domain.models import Regime
from trading_copilot.services.bybit_public import BybitPublicClient
from trading_copilot.services.indicators import ema, stoch_rsi
from trading_copilot.services.regime import classify_regime


def _book_metrics(book: dict) -> dict:
    bids = [(float(p), float(q)) for p, q in book["b"]]
    asks = [(float(p), float(q)) for p, q in book["a"]]
    bid_depth = sum(p * q for p, q in bids)
    ask_depth = sum(p * q for p, q in asks)
    total = bid_depth + ask_depth
    imbalance = 0.0 if total == 0 else (bid_depth - ask_depth) / total
    return {
        "best_bid": bids[0][0] if bids else None,
        "best_ask": asks[0][0] if asks else None,
        "bid_depth_usdt": bid_depth,
        "ask_depth_usdt": ask_depth,
        "book_imbalance": imbalance,
    }


async def build_market_snapshot(symbol: str, client: BybitPublicClient | None = None) -> dict:
    client = client or BybitPublicClient()
    ticker, klines_1h, klines_4h, book, funding = await __import__("asyncio").gather(
        client.ticker(symbol),
        client.klines(symbol, "60", 200),
        client.klines(symbol, "240", 200),
        client.orderbook(symbol, 50),
        client.funding_history(symbol, 1),
    )

    def features(rows: list[list[str]]) -> dict:
        closes = [float(row[4]) for row in rows]
        e12 = ema(closes, 12)
        e21 = ema(closes, 21)
        k, d = stoch_rsi(closes)
        regime: Regime = classify_regime(e12[-1], e21[-1], e12[-2], e21[-2])
        return {
            "ema12": e12[-1],
            "ema21": e21[-1],
            "stoch_rsi_k": k[-1],
            "stoch_rsi_d": d[-1],
            "regime": regime,
        }

    return {
        "symbol": symbol,
        "last_price": float(ticker["lastPrice"]),
        "price24h_pct": float(ticker["price24hPcnt"]),
        "funding_rate": float(funding[0]["fundingRate"]) if funding else None,
        "timeframes": {"1h": features(klines_1h), "4h": features(klines_4h)},
        "orderbook": _book_metrics(book),
    }
