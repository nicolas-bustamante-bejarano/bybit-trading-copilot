import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.api.journal import router as journal_router
from trading_copilot.config import settings
from trading_copilot.domain.execution import AddProjectionRequest, ExecutionPlanRequest
from trading_copilot.domain.models import (
    FibRequest,
    PortfolioRiskRequest,
    PositionRiskRequest,
    PositionRiskResult,
)
from trading_copilot.domain.playbook import PlaybookEvaluationRequest
from trading_copilot.domain.position_coach import PositionCoach
from trading_copilot.persistence.database import get_session
from trading_copilot.services.account_normalizer import (
    normalize_account_snapshot,
    portfolio_live_view,
)
from trading_copilot.services.bybit_private import BybitReadOnlyClient
from trading_copilot.services.bybit_ws import BybitLinearStream
from trading_copilot.services.execution import project_add, summarize_execution_plan
from trading_copilot.services.indicators import fib_retracements
from trading_copilot.services.lifecycle import reconstruct_open_position_lifecycle
from trading_copilot.services.live_market import LiveMarketStore
from trading_copilot.services.market_snapshot import build_market_snapshot
from trading_copilot.services.playbook import evaluate_playbook
from trading_copilot.services.position_coach import evaluate_position_coach, load_coach_records
from trading_copilot.services.reaction import ReactionThresholds
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


app = FastAPI(title="Bybit Trading Copilot", version="0.8.0", lifespan=lifespan)
app.include_router(journal_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/account/status")
def account_status() -> dict:
    return {
        "enabled": settings.bybit_read_only_sync_enabled,
        "credentials_configured": settings.has_read_only_credentials,
        "mode": "read_only",
    }


def _read_only_client() -> BybitReadOnlyClient:
    if not settings.bybit_read_only_sync_enabled:
        raise HTTPException(status_code=503, detail="Read-only Bybit account sync is disabled")
    if not settings.has_read_only_credentials:
        raise HTTPException(
            status_code=503, detail="Read-only Bybit credentials are not configured"
        )
    return BybitReadOnlyClient(
        api_key=settings.bybit_api_key or "",
        api_secret=settings.bybit_api_secret or "",
        base_url=settings.bybit_base_url,
    )


async def _normalized_account():
    snapshot = await _read_only_client().account_snapshot()
    return normalize_account_snapshot(
        snapshot,
        max_group_risk_pct=settings.default_max_portfolio_risk_pct,
    )


@app.get("/account/snapshot")
async def account_snapshot() -> dict:
    try:
        return await _read_only_client().account_snapshot()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/account/normalized")
async def account_normalized() -> dict:
    try:
        return (await _normalized_account()).model_dump(mode="json")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/portfolio/live")
async def portfolio_live() -> dict:
    try:
        return portfolio_live_view(await _normalized_account())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/positions/{symbol}/lifecycle")
async def position_lifecycle(symbol: str) -> dict:
    try:
        account = await _normalized_account()
        normalized_symbol = symbol.upper()
        position = next(
            (item for item in account.positions if item.symbol == normalized_symbol),
            None,
        )
        if position is None:
            raise HTTPException(status_code=404, detail=f"No open position for {normalized_symbol}")
        lifecycle = reconstruct_open_position_lifecycle(position, account.recent_fills)
        return lifecycle.model_dump(mode="json")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/positions/{symbol}/coach", response_model=PositionCoach)
async def position_coach(
    symbol: str, session: AsyncSession = Depends(get_session)
) -> PositionCoach:
    try:
        normalized_symbol = symbol.upper()
        account = await _normalized_account()
        position = next(
            (item for item in account.positions if item.symbol == normalized_symbol), None
        )
        if position is None:
            raise HTTPException(status_code=404, detail=f"No open position for {normalized_symbol}")
        try:
            plan, rules, fibs, ranges, plans_by_symbol = await load_coach_records(
                session, normalized_symbol
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        lifecycle = reconstruct_open_position_lifecycle(position, account.recent_fills)
        try:
            market = await build_market_snapshot(normalized_symbol)
        except Exception:  # noqa: BLE001 - market evidence degrades to missing
            market = None
        live = None
        if live_market.has_data(normalized_symbol):
            live = live_market.state(normalized_symbol)
            reaction = live_market.reaction_state(normalized_symbol, 60_000)
            live["reaction_1m"] = reaction["reaction"]
            if reaction["bar"]:
                live["timestamp_ms"] = reaction["bar"]["end_ms"]
        return evaluate_position_coach(
            position=position,
            account=account,
            lifecycle=lifecycle,
            plan=plan,
            rules=rules,
            plans_by_symbol=plans_by_symbol,
            market=market,
            live=live,
            fibs=fibs,
            ranges=ranges,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/status")
def live_status() -> dict:
    return {
        "enabled": settings.live_stream_enabled,
        "configured_symbols": settings.stream_symbols,
        "active_symbols": live_market.active_symbols(),
    }


def _require_live_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    if not live_market.has_data(symbol):
        raise HTTPException(
            status_code=503,
            detail=(
                f"No live state for {symbol}. Set LIVE_STREAM_ENABLED=true and include the symbol "
                "in LIVE_STREAM_SYMBOLS."
            ),
        )
    return symbol


@app.get("/live/{symbol}/state")
def live_state(symbol: str) -> dict:
    return live_market.state(_require_live_symbol(symbol))


@app.get("/live/{symbol}/reaction")
def live_reaction(
    symbol: str,
    interval_ms: int = Query(default=60_000),
    min_total_notional: float = Query(default=25_000.0, ge=0),
    min_flow_imbalance: float = Query(default=0.30, ge=0, le=1),
    max_absorption_progress_bps: float = Query(default=3.0, ge=0),
    min_continuation_progress_bps: float = Query(default=5.0, ge=0),
) -> dict:
    symbol = _require_live_symbol(symbol)
    if interval_ms not in {1_000, 60_000}:
        raise HTTPException(status_code=400, detail="interval_ms must be 1000 or 60000")
    thresholds = ReactionThresholds(
        min_total_notional=min_total_notional,
        min_flow_imbalance=min_flow_imbalance,
        max_absorption_progress_bps=max_absorption_progress_bps,
        min_continuation_progress_bps=min_continuation_progress_bps,
    )
    return live_market.reaction_state(symbol, interval_ms, thresholds)


@app.post("/playbook/evaluate")
def playbook_evaluate(request: PlaybookEvaluationRequest) -> dict:
    if request.reaction_state is None and live_market.has_data(request.symbol):
        live_reaction_state = live_market.reaction_state(request.symbol, 60_000).get("reaction")
        if live_reaction_state is not None:
            request = request.model_copy(update={"reaction_state": live_reaction_state["state"]})
    return evaluate_playbook(request)


@app.post("/execution/plan")
def execution_plan(request: ExecutionPlanRequest) -> dict:
    try:
        return summarize_execution_plan(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/execution/project-add")
def execution_project_add(request: AddProjectionRequest) -> dict:
    try:
        return project_add(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
