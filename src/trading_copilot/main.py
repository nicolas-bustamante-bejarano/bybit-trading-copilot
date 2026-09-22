from fastapi import FastAPI, HTTPException

from trading_copilot.domain.models import (
    FibRequest,
    PortfolioRiskRequest,
    PositionRiskRequest,
    PositionRiskResult,
)
from trading_copilot.services.indicators import fib_retracements
from trading_copilot.services.market_snapshot import build_market_snapshot
from trading_copilot.services.risk import max_position_size, portfolio_risk_summary

app = FastAPI(title="Bybit Trading Copilot", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
