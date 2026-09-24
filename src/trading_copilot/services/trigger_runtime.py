from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.trigger import LowerTimeframeTriggerSnapshot, TriggerState
from trading_copilot.persistence.models import (
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TriggerAttemptRow,
    WatchedSetupRow,
)
from trading_copilot.services.trigger_context import (
    TriggerContextResolution,
    compose_trigger_request,
    resolve_trigger_context,
)
from trading_copilot.services.trigger_engine import evaluate_lower_timeframe_trigger
from trading_copilot.services.trigger_persistence import persist_trigger_result
from trading_copilot.services.trigger_snapshot import FIFTEEN_MINUTES_MS, FIVE_MINUTES_MS


@dataclass(frozen=True)
class TriggerCandidateOutcome:
    evaluated: bool = False
    persisted: bool = False
    confirmed: bool = False
    terminal: bool = False
    failure: str | None = None


async def _lock_prerequisites(
    session: AsyncSession, watched_setup_id: str
) -> tuple[WatchedSetupRow | None, ScannerWatchlistRow | None]:
    symbol = await session.scalar(
        select(WatchedSetupRow.symbol).where(WatchedSetupRow.id == watched_setup_id)
    )
    if symbol is None:
        return None, None
    watchlist = await session.scalar(
        select(ScannerWatchlistRow)
        .where(ScannerWatchlistRow.symbol == symbol)
        .with_for_update()
    )
    watched = await session.scalar(
        select(WatchedSetupRow)
        .where(WatchedSetupRow.id == watched_setup_id)
        .with_for_update()
    )
    if watched is None or watched.symbol != symbol:
        return None, watchlist
    return watched, watchlist


def snapshot_covers_context(
    snapshot: LowerTimeframeTriggerSnapshot, context: TriggerContextResolution
) -> bool:
    if context.armed_at is None or not snapshot.bars_5m or not snapshot.bars_15m:
        return False
    armed_ms = int(context.armed_at.timestamp() * 1000)
    first_5m_start = ((armed_ms + FIVE_MINUTES_MS - 1) // FIVE_MINUTES_MS) * FIVE_MINUTES_MS
    first_15m_start = (armed_ms // FIFTEEN_MINUTES_MS) * FIFTEEN_MINUTES_MS
    return (
        min(bar.start_ms for bar in snapshot.bars_5m) <= first_5m_start
        and min(bar.start_ms for bar in snapshot.bars_15m) <= first_15m_start
    )


async def evaluate_current_trigger_candidate(
    session: AsyncSession,
    *,
    watched_setup_id: str,
    expected_arm_key: str,
    snapshot: LowerTimeframeTriggerSnapshot,
) -> TriggerCandidateOutcome:
    watched, watchlist = await _lock_prerequisites(session, watched_setup_id)
    if watched is None:
        return TriggerCandidateOutcome(failure="CANDIDATE_UNAVAILABLE")
    if watchlist is None:
        return TriggerCandidateOutcome(failure="WATCHLIST_CONFIG_REQUIRED")
    transitions = list(
        await session.scalars(
            select(ScannerTransitionRow).where(
                ScannerTransitionRow.watched_setup_id == watched.id
            )
        )
    )
    current = resolve_trigger_context(
        watched=watched, watchlist=watchlist, transitions=transitions
    )
    if not current.eligible:
        return TriggerCandidateOutcome(failure=current.blocking_reason or "NOT_ELIGIBLE")
    if current.arm_key != expected_arm_key:
        return TriggerCandidateOutcome(failure="ARM_CHANGED_DURING_CYCLE")
    attempt = await session.scalar(
        select(TriggerAttemptRow)
        .where(
            TriggerAttemptRow.watched_setup_id == watched.id,
            TriggerAttemptRow.arm_key == current.arm_key,
        )
        .with_for_update()
    )
    context = resolve_trigger_context(
        watched=watched,
        watchlist=watchlist,
        transitions=transitions,
        existing_attempt=attempt,
    )
    if not context.eligible:
        return TriggerCandidateOutcome(
            failure=context.blocking_reason or "TRIGGER_ATTEMPT_CONTEXT_INVALID"
        )
    if attempt is not None and attempt.state == TriggerState.CONFIRMED.value:
        return TriggerCandidateOutcome(terminal=True)
    if not snapshot_covers_context(snapshot, context):
        return TriggerCandidateOutcome(failure="TRIGGER_HISTORY_INCOMPLETE")
    request = compose_trigger_request(context, snapshot)
    result = evaluate_lower_timeframe_trigger(request)
    await persist_trigger_result(session, context=context, result=result)
    return TriggerCandidateOutcome(
        evaluated=True,
        persisted=True,
        confirmed=result.state == TriggerState.CONFIRMED,
    )
