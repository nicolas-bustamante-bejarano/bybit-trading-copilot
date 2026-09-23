import asyncio
from contextlib import asynccontextmanager, suppress
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.api.journal import router as journal_router
from trading_copilot.api.state_changes import router as state_changes_router
from trading_copilot.api.state_changes import set_status_provider
from trading_copilot.api.workspace import router as workspace_router
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
from trading_copilot.domain.workspace import SizingRequest
from trading_copilot.persistence.database import get_session, session_factory
from trading_copilot.persistence.models import DecisionSnapshotRow, ExecutionEventRow, TradePlanRow
from trading_copilot.services.account_normalizer import (
    normalize_account_snapshot,
)
from trading_copilot.services.bybit_private import BybitReadOnlyClient
from trading_copilot.services.bybit_public import BybitPublicClient
from trading_copilot.services.bybit_ws import BybitLinearStream
from trading_copilot.services.chart import aggregate_daily_to_3d
from trading_copilot.services.execution import project_add, summarize_execution_plan
from trading_copilot.services.indicators import fib_retracements
from trading_copilot.services.lifecycle import reconstruct_open_position_lifecycle
from trading_copilot.services.live_market import LiveMarketStore
from trading_copilot.services.market_snapshot import build_market_snapshot
from trading_copilot.services.playbook import evaluate_playbook
from trading_copilot.services.position_coach import evaluate_position_coach, load_coach_records
from trading_copilot.services.reaction import ReactionThresholds
from trading_copilot.services.risk import max_position_size, portfolio_risk_summary
from trading_copilot.services.scanner_composer import evaluate_symbol
from trading_copilot.services.scanner_monitor import SetupScannerMonitor
from trading_copilot.services.scanner_snapshot import build_scanner_snapshot
from trading_copilot.services.sizing import build_sizing_plan
from trading_copilot.services.state_change_monitor import StateChangeMonitor
from trading_copilot.services.structural_risk import structural_risk_summary

live_market = LiveMarketStore()
live_stream: BybitLinearStream | None = None
live_stream_task: asyncio.Task | None = None
state_change_monitor: StateChangeMonitor | None = None
state_change_monitor_task: asyncio.Task | None = None
setup_scanner_monitor: SetupScannerMonitor | None = None
setup_scanner_monitor_task: asyncio.Task | None = None


async def _build_scanner_snapshot(symbol: str, evaluated_at):
    return await build_scanner_snapshot(
        symbol,
        evaluated_at=evaluated_at,
        live_market=live_market,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global live_stream, live_stream_task, state_change_monitor, state_change_monitor_task
    global setup_scanner_monitor, setup_scanner_monitor_task
    setup_scanner_monitor_task = None
    if settings.live_stream_enabled:
        live_stream = BybitLinearStream(
            settings.stream_symbols,
            url=settings.bybit_ws_linear_url,
            orderbook_depth=50,
        )
        live_stream_task = asyncio.create_task(live_stream.run(live_market.handle))
    state_change_monitor = StateChangeMonitor(
        enabled=settings.state_change_monitor_enabled,
        interval_seconds=settings.state_change_monitor_interval_seconds,
        sessions=session_factory,
        fetch_account=_normalized_account,
        compose_coach=compose_monitored_position_coach,
    )
    set_status_provider(state_change_monitor.status)
    if settings.state_change_monitor_enabled:
        state_change_monitor_task = asyncio.create_task(state_change_monitor.run())
    setup_scanner_monitor = SetupScannerMonitor(
        enabled=settings.setup_scanner_enabled,
        interval_seconds=settings.setup_scanner_interval_seconds,
        concurrency=settings.setup_scanner_concurrency,
        sessions=session_factory,
        build_snapshot=_build_scanner_snapshot,
        compose_symbol=evaluate_symbol,
    )
    if settings.setup_scanner_enabled:
        setup_scanner_monitor_task = asyncio.create_task(setup_scanner_monitor.run())
    try:
        yield
    finally:
        if live_stream is not None:
            await live_stream.stop()
        if live_stream_task is not None:
            live_stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await live_stream_task
        if state_change_monitor_task is not None:
            state_change_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await state_change_monitor_task
        if setup_scanner_monitor_task is not None:
            setup_scanner_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await setup_scanner_monitor_task
            setup_scanner_monitor_task = None


app = FastAPI(title="Bybit Trading Copilot", version="0.8.0", lifespan=lifespan)
app.include_router(journal_router)
app.include_router(workspace_router)
app.include_router(state_changes_router)


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


async def compose_position_coach(
    account,
    position,
    session: AsyncSession,
    *,
    degrade_market_failure: bool = True,
    require_live_data: bool = False,
) -> PositionCoach:
    normalized_symbol = position.symbol.upper()
    plan, rules, fibs, ranges, plans_by_symbol = await load_coach_records(
        session, normalized_symbol
    )
    if plan and plan.side.upper() != position.side.value.upper():
        raise ValueError("Active trade plan side does not match the live position side")
    lifecycle = reconstruct_open_position_lifecycle(position, account.recent_fills)
    try:
        market = await build_market_snapshot(normalized_symbol)
    except Exception:
        if not degrade_market_failure:
            raise
        market = None
    live = None
    if require_live_data and not live_market.has_data(normalized_symbol):
        raise RuntimeError(f"Live market state unavailable for {normalized_symbol}")
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


async def compose_monitored_position_coach(account, position, session: AsyncSession) -> PositionCoach:
    return await compose_position_coach(
        account,
        position,
        session,
        degrade_market_failure=False,
        require_live_data=settings.live_stream_enabled,
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
async def portfolio_live(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        account = await _normalized_account()
        plans = list(
            (
                await session.scalars(
                    select(TradePlanRow).where(TradePlanRow.lifecycle_status == "ACTIVE")
                )
            ).all()
        )
        unique: dict[str, TradePlanRow] = {}
        ambiguous: set[str] = set()
        for plan in plans:
            if plan.symbol in unique:
                ambiguous.add(plan.symbol)
            else:
                unique[plan.symbol] = plan
        for symbol in ambiguous:
            unique.pop(symbol, None)
        return structural_risk_summary(account, unique)
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
            return await compose_position_coach(account, position, session)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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


@app.get("/market/{symbol}/chart")
async def market_chart(
    symbol: str, timeframe: str = Query(default="1h"), limit: int = Query(default=300, ge=50, le=1000)
) -> dict:
    intervals = {"5m": "5", "15m": "15", "1h": "60", "4h": "240", "1D": "D", "3D": "D"}
    if timeframe not in intervals:
        raise HTTPException(422, "Unsupported timeframe")
    try:
        rows = await BybitPublicClient().klines(symbol.upper(), intervals[timeframe], limit * (3 if timeframe == "3D" else 1))
        if timeframe == "3D":
            rows = aggregate_daily_to_3d(rows)
        from trading_copilot.services.indicators import ema

        closes = [float(row[4]) for row in rows]
        e12, e21 = ema(closes, 12), ema(closes, 21)
        candles = [
            {"time": int(row[0]) // 1000, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]), "ema12": e12[i], "ema21": e21[i]}
            for i, row in enumerate(rows)
        ]
        return {"symbol": symbol.upper(), "timeframe": timeframe, "candles": candles}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/workspace/sizing")
async def workspace_sizing(request: SizingRequest, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        account = await _normalized_account()
        active = list(
            (await session.scalars(select(TradePlanRow).where(TradePlanRow.lifecycle_status == "ACTIVE"))).all()
        )
        matching = [plan for plan in active if plan.symbol == request.symbol.upper()]
        if len(matching) > 1:
            raise HTTPException(409, "Multiple active trade plans exist for this symbol")
        plan = matching[0] if matching else None
        if plan is not None and plan.side.upper() != request.side:
            raise HTTPException(409, "Active trade plan side does not match sizing request")
        if request.trade_plan_id is not None and (plan is None or plan.id != request.trade_plan_id):
            raise HTTPException(409, "Requested trade plan is not the single active plan for this symbol")
        if plan is not None:
            request = request.model_copy(
                update={
                    "max_risk_percent": plan.max_risk_percent,
                    "correlation_group": plan.correlation_group,
                    "stages": _plan_stages(plan),
                }
            )
        if not request.correlation_group:
            raise HTTPException(422, "Correlation group is required when no active trade plan exists")
        unique: dict[str, TradePlanRow] = {}
        ambiguous: set[str] = set()
        for active_plan in active:
            if active_plan.symbol in unique:
                ambiguous.add(active_plan.symbol)
            else:
                unique[active_plan.symbol] = active_plan
        for symbol in ambiguous:
            unique.pop(symbol, None)
        risk = structural_risk_summary(account, unique)
        group = request.correlation_group
        group_data = risk["correlation_groups"].get(group, {"known_risk_usdt": Decimal(), "unknown": []})
        if plan is not None:
            events = list(
                (await session.scalars(select(ExecutionEventRow).where(ExecutionEventRow.trade_plan_id == plan.id).order_by(ExecutionEventRow.timestamp))).all()
            )
            executed = [event for event in events if event.event_type in {"PROBE", "ADD"}]
            if executed:
                last = executed[-1]
                snapshot = await session.get(DecisionSnapshotRow, last.decision_snapshot_id) if last.decision_snapshot_id else None
                request = request.model_copy(update={
                    "completed_stage_count": len(executed),
                    "prior_stage_baseline_trusted": snapshot is not None,
                    "prior_evidence": snapshot.evidence_present if snapshot else [],
                })
            position = next((item for item in account.positions if item.symbol == plan.symbol), None)
            if position is not None:
                coach = await compose_position_coach(account, position, session)
                canonical = coach.execution.condition_evidence
                request = request.model_copy(update={"current_evidence": canonical})
            else:
                request = request.model_copy(update={"current_evidence": []})
        instrument = await BybitPublicClient().instrument(request.symbol.upper())
        lot = instrument.get("lotSizeFilter", {})
        return build_sizing_plan(
            request, equity=Decimal(str(account.equity_usdt)),
            available_margin=Decimal(str(account.available_balance_usdt)) if account.available_balance_usdt is not None else None,
            group_risk=Decimal(str(group_data["known_risk_usdt"])), group_unknown=bool(group_data["unknown"]),
            qty_step=Decimal(lot.get("qtyStep", "1")), min_qty=Decimal(lot.get("minOrderQty", "0")),
            min_notional=Decimal(lot.get("minNotionalValue", "0")),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _plan_stages(plan: TradePlanRow):
    """Read explicit plan stages only; do not invent a 50/50 add schedule."""
    from trading_copilot.domain.workspace import SizingStage

    configured = (plan.entry_probe_plan or {}).get("stages", [])
    if not configured:
        return []
    return [SizingStage.model_validate(stage) for stage in configured]


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
