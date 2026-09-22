import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException

from trading_copilot.config import settings
from trading_copilot.domain.models import (
    FibRequest,
    PortfolioRiskRequest,
    PositionRiskRequest,
    PositionRiskResult,
)
from trading_copilot.services.bybit_ws import BybitLinearStream
from trading_copilot.services.indicators import fib_retracements
from trading_copilot.services.live_market import LiveMarketStore
from trading_copilot.services.market_snapshot import build_market_snapshot
from trading_copilot.services.risk import max_position_size, portfolio_risk_summary

live_market = LiveMarketStore()
live_stream: BybitLinearStream | None = None
live_stream_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global live_stream, live_stream_task
    if settings.live_stream_enabled:
        live_stream = BybitLinearStream(
            settings.stream_symbols,
            url=settings.bybit_ws_linear_url,
            orderbook_depth=50,
        )
        live_stream_task = asyncio.create_task(live_stream.run(live_market.handle))
    try:
        yield
    finally:
        if live_stream is not None:
            await live_stream.stop()
        if live_stream_task is not None:
            live_stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await live_stream_task


app = FastAPI(title="Bybit Trading Copilot", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/live/status")
def live_status() -> dict:
    return {
        "enabled": settings.live_stream_enabled,
        "configured_symbols": settings.stream_symbols,
        "active_symbols": live_market.active_symbols(),
    }


@app.get("/live/{symbol}/state")
def live_state(symbol: str) -> dict:
    symbol = symbol.upper()
    if not live_market.has_data(symbol):
        raise HTTPException(
            status_code=503,
            detail=(
                f"No live state for {symbol}. Set LIVE_STREAM_ENABLED=true and include the symbol "
                "in LIVE_STREAM_SYMBOLS."
            ),
        )
    return live_market.state(symbol)


@app.get("/market/{symbol}/snapshot")
async def market_snapshot(symbol: str) -> dict:
    try:
        return await build_market_snapshot(symbol.upper())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/analysis/fib")
def calculate_fib(request: FibRequest) -> dict:
    return {
        "direction": request.direction,
        "levels": fib_retracements(request.swing_low, request.swing_high, request.direction.value),
    }


@app.post("/risk/position-size", response_model=PositionRiskResult)
def position_size(request: PositionRiskRequest) -> PositionRiskResult:
    try:
        budget, per_unit, qty = max_position_size(
            request.account_equity,
            request.max_risk_pct,
            request.side,
            request.entry,
            request.stop,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PositionRiskResult(risk_budget=budget, risk_per_unit=per_unit, max_quantity=qty)


@app.post("/risk/portfolio")
def portfolio_risk(request: PortfolioRiskRequest) -> dict:
    try:
        result = portfolio_risk_summary(request.account_equity, request.positions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["max_portfolio_risk_usdt"] = request.account_equity * request.max_portfolio_risk_pct
    result["within_budget"] = result["total_risk_pct"] <= request.max_portfolio_risk_pct
    return result
