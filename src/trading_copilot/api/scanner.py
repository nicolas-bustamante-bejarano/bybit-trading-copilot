from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.scanner import (
    ScannerMonitorStatusResponse,
    ScannerSetupResponse,
    ScannerSetupType,
    ScannerStatus,
    ScannerTransitionResponse,
    ScannerWatchlistCreate,
    ScannerWatchlistPatch,
    ScannerWatchlistResponse,
)
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    ScannerTransitionRow,
    ScannerWatchlistRow,
    WatchedSetupRow,
)
from trading_copilot.services.scanner_composer import SymbolEvaluationOutcome

router = APIRouter(prefix="/scanner", tags=["scanner"])
_status_provider: Callable[[], object] | None = None
_reevaluate_provider: Callable[[str], Awaitable[SymbolEvaluationOutcome]] | None = None


def set_status_provider(provider: Callable[[], object] | None) -> None:
    global _status_provider
    _status_provider = provider


def set_reevaluate_provider(
    provider: Callable[[str], Awaitable[SymbolEvaluationOutcome]] | None,
) -> None:
    global _reevaluate_provider
    _reevaluate_provider = provider


@router.get("/status", response_model=ScannerMonitorStatusResponse)
def scanner_status() -> ScannerMonitorStatusResponse:
    if _status_provider is None:
        return ScannerMonitorStatusResponse(
            enabled=False,
            running=False,
            interval_seconds=30,
        )
    return ScannerMonitorStatusResponse.model_validate(_status_provider(), from_attributes=True)


@router.post("/reevaluate/{symbol}")
async def reevaluate_symbol(symbol: str) -> dict:
    if _reevaluate_provider is None:
        raise HTTPException(503, "Scanner reevaluation is unavailable")
    try:
        outcome = await _reevaluate_provider(symbol.upper())
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "symbol": symbol.upper(),
        "setup_count": len(outcome.results),
        "failed_setups": outcome.failed_setups,
    }


@router.get("/watchlist", response_model=list[ScannerWatchlistResponse])
async def list_watchlist(
    session: AsyncSession = Depends(get_session),
) -> list[ScannerWatchlistRow]:
    return list(
        await session.scalars(
            select(ScannerWatchlistRow).order_by(ScannerWatchlistRow.symbol)
        )
    )


@router.post("/watchlist", response_model=ScannerWatchlistResponse, status_code=201)
async def create_watchlist_item(
    body: ScannerWatchlistCreate,
    session: AsyncSession = Depends(get_session),
) -> ScannerWatchlistRow:
    row = ScannerWatchlistRow(**body.model_dump())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, f"Scanner watchlist already contains {body.symbol}") from exc
    except Exception:
        await session.rollback()
        raise
    await session.refresh(row)
    return row


async def _require_watchlist(session: AsyncSession, symbol: str) -> ScannerWatchlistRow:
    row = await session.scalar(
        select(ScannerWatchlistRow).where(ScannerWatchlistRow.symbol == symbol.upper())
    )
    if row is None:
        raise HTTPException(404, "Scanner watchlist symbol not found")
    return row


@router.patch("/watchlist/{symbol}", response_model=ScannerWatchlistResponse)
async def patch_watchlist_item(
    symbol: str,
    body: ScannerWatchlistPatch,
    session: AsyncSession = Depends(get_session),
) -> ScannerWatchlistRow:
    row = await _require_watchlist(session, symbol)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Scanner watchlist update conflicts with existing data") from exc
    except Exception:
        await session.rollback()
        raise
    await session.refresh(row)
    return row


@router.delete("/watchlist/{symbol}", status_code=204)
async def delete_watchlist_item(
    symbol: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await _require_watchlist(session, symbol)
    await session.delete(row)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return Response(status_code=204)


@router.get("/setups", response_model=list[ScannerSetupResponse])
async def list_setups(
    symbol: str | None = None,
    setup_type: ScannerSetupType | None = None,
    status: ScannerStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[WatchedSetupRow]:
    query = select(WatchedSetupRow).order_by(
        WatchedSetupRow.symbol,
        WatchedSetupRow.setup_type,
    )
    if symbol:
        query = query.where(WatchedSetupRow.symbol == symbol.upper())
    if setup_type:
        query = query.where(WatchedSetupRow.setup_type == setup_type.value)
    if status:
        query = query.where(WatchedSetupRow.status == status.value)
    return list(await session.scalars(query.limit(limit)))


@router.get(
    "/setups/{symbol}/{setup_type}",
    response_model=ScannerSetupResponse,
)
async def get_setup(
    symbol: str,
    setup_type: ScannerSetupType,
    session: AsyncSession = Depends(get_session),
) -> WatchedSetupRow:
    row = await session.scalar(
        select(WatchedSetupRow).where(
            WatchedSetupRow.symbol == symbol.upper(),
            WatchedSetupRow.setup_type == setup_type.value,
        )
    )
    if row is None:
        raise HTTPException(404, "Scanner setup not found")
    return row


@router.get("/transitions", response_model=list[ScannerTransitionResponse])
async def list_transitions(
    symbol: str | None = None,
    setup_type: ScannerSetupType | None = None,
    watched_setup_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[ScannerTransitionRow]:
    query = select(ScannerTransitionRow).order_by(
        ScannerTransitionRow.timestamp.desc(),
        ScannerTransitionRow.version.desc(),
    )
    if symbol:
        query = query.where(ScannerTransitionRow.symbol == symbol.upper())
    if setup_type:
        query = query.where(ScannerTransitionRow.setup_type == setup_type.value)
    if watched_setup_id:
        query = query.where(ScannerTransitionRow.watched_setup_id == watched_setup_id)
    return list(await session.scalars(query.limit(limit)))
