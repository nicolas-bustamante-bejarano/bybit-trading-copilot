from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.scanner import ScannerSetupType, ScannerStatus
from trading_copilot.domain.trigger import (
    CurrentTriggerResponse,
    TriggerAttemptResponse,
    TriggerMonitorStatusResponse,
    TriggerState,
    TriggerTransitionResponse,
)
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TriggerAttemptRow,
    TriggerTransitionRow,
    WatchedSetupRow,
)
from trading_copilot.services.trigger_context import resolve_trigger_context

router = APIRouter(prefix="/trigger", tags=["trigger"])
_status_provider: Callable[[], object] | None = None


def set_trigger_status_provider(provider: Callable[[], object] | None) -> None:
    global _status_provider
    _status_provider = provider


@router.get("/status", response_model=TriggerMonitorStatusResponse)
def trigger_status() -> TriggerMonitorStatusResponse:
    if _status_provider is None:
        return TriggerMonitorStatusResponse(enabled=False, running=False, interval_seconds=15)
    return TriggerMonitorStatusResponse.model_validate(
        _status_provider(), from_attributes=True
    )


@router.get("/attempts", response_model=list[TriggerAttemptResponse])
async def list_attempts(
    symbol: str | None = None,
    setup_type: ScannerSetupType | None = None,
    state: TriggerState | None = None,
    watched_setup_id: str | None = None,
    arm_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[TriggerAttemptRow]:
    query = select(TriggerAttemptRow).order_by(
        TriggerAttemptRow.armed_at.desc(),
        TriggerAttemptRow.created_at.desc(),
        TriggerAttemptRow.id.desc(),
    )
    if symbol:
        query = query.where(TriggerAttemptRow.symbol == symbol.upper())
    if setup_type:
        query = query.where(TriggerAttemptRow.setup_type == setup_type.value)
    if state:
        query = query.where(TriggerAttemptRow.state == state.value)
    if watched_setup_id:
        query = query.where(TriggerAttemptRow.watched_setup_id == watched_setup_id)
    if arm_key:
        query = query.where(TriggerAttemptRow.arm_key == arm_key)
    return list(await session.scalars(query.limit(limit)))


@router.get("/attempts/{attempt_id}", response_model=TriggerAttemptResponse)
async def get_attempt(
    attempt_id: str, session: AsyncSession = Depends(get_session)
) -> TriggerAttemptRow:
    row = await session.get(TriggerAttemptRow, attempt_id)
    if row is None:
        raise HTTPException(404, "Trigger attempt not found")
    return row


@router.get("/transitions", response_model=list[TriggerTransitionResponse])
async def list_transitions(
    trigger_attempt_id: str | None = None,
    symbol: str | None = None,
    setup_type: ScannerSetupType | None = None,
    to_state: TriggerState | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[TriggerTransitionRow]:
    query = select(TriggerTransitionRow).order_by(
        TriggerTransitionRow.timestamp.desc(),
        TriggerTransitionRow.version.desc(),
        TriggerTransitionRow.id.desc(),
    )
    if trigger_attempt_id:
        query = query.where(
            TriggerTransitionRow.trigger_attempt_id == trigger_attempt_id
        )
    if symbol:
        query = query.where(TriggerTransitionRow.symbol == symbol.upper())
    if setup_type:
        query = query.where(TriggerTransitionRow.setup_type == setup_type.value)
    if to_state:
        query = query.where(TriggerTransitionRow.to_state == to_state.value)
    return list(await session.scalars(query.limit(limit)))


@router.get("/current", response_model=list[CurrentTriggerResponse])
async def list_current_triggers(
    symbol: str | None = None,
    setup_type: ScannerSetupType | None = None,
    watched_setup_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[CurrentTriggerResponse]:
    query = (
        select(WatchedSetupRow)
        .where(WatchedSetupRow.status == ScannerStatus.TRIGGER_ARMED.value)
        .order_by(WatchedSetupRow.symbol, WatchedSetupRow.setup_type, WatchedSetupRow.id)
    )
    if symbol:
        query = query.where(WatchedSetupRow.symbol == symbol.upper())
    if setup_type:
        query = query.where(WatchedSetupRow.setup_type == setup_type.value)
    if watched_setup_id:
        query = query.where(WatchedSetupRow.id == watched_setup_id)
    watched_rows = list(await session.scalars(query.limit(limit)))
    if not watched_rows:
        return []
    setup_ids = [row.id for row in watched_rows]
    symbols = {row.symbol for row in watched_rows}
    watchlists = list(
        await session.scalars(
            select(ScannerWatchlistRow).where(ScannerWatchlistRow.symbol.in_(symbols))
        )
    )
    transitions = list(
        await session.scalars(
            select(ScannerTransitionRow).where(
                ScannerTransitionRow.watched_setup_id.in_(setup_ids)
            )
        )
    )
    attempts = list(
        await session.scalars(
            select(TriggerAttemptRow).where(
                TriggerAttemptRow.watched_setup_id.in_(setup_ids)
            )
        )
    )
    watchlist_by_symbol = {row.symbol: row for row in watchlists}
    transitions_by_setup: dict[str, list[ScannerTransitionRow]] = defaultdict(list)
    for transition in transitions:
        transitions_by_setup[transition.watched_setup_id].append(transition)
    attempts_by_identity = {
        (attempt.watched_setup_id, attempt.arm_key): attempt for attempt in attempts
    }
    responses: list[CurrentTriggerResponse] = []
    for watched in watched_rows:
        watchlist = watchlist_by_symbol.get(watched.symbol)
        if watchlist is None:
            responses.append(
                CurrentTriggerResponse(
                    watched_setup_id=watched.id,
                    symbol=watched.symbol,
                    setup_type=ScannerSetupType(watched.setup_type),
                    scanner_status=watched.status,
                    eligible=False,
                    blocking_reason="WATCHLIST_CONFIG_REQUIRED",
                )
            )
            continue
        preliminary = resolve_trigger_context(
            watched=watched,
            watchlist=watchlist,
            transitions=transitions_by_setup[watched.id],
        )
        attempt = (
            attempts_by_identity.get((watched.id, preliminary.arm_key))
            if preliminary.arm_key
            else None
        )
        final = (
            resolve_trigger_context(
                watched=watched,
                watchlist=watchlist,
                transitions=transitions_by_setup[watched.id],
                existing_attempt=attempt,
            )
            if preliminary.eligible
            else preliminary
        )
        responses.append(
            CurrentTriggerResponse(
                watched_setup_id=watched.id,
                symbol=watched.symbol,
                setup_type=final.setup_type,
                scanner_status=watched.status,
                eligible=final.eligible,
                blocking_reason=final.blocking_reason,
                arm_key=preliminary.arm_key,
                arm_source=preliminary.arm_source,
                armed_at=preliminary.armed_at,
                reference_level=preliminary.reference_level,
                reference_source=preliminary.reference_source,
                attempt=(
                    TriggerAttemptResponse.model_validate(attempt, from_attributes=True)
                    if final.eligible and attempt is not None
                    else None
                ),
                terminal=(
                    final.eligible
                    and attempt is not None
                    and attempt.state == TriggerState.CONFIRMED.value
                ),
            )
        )
    return responses
