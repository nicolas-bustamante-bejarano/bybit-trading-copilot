from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_copilot.domain.scanner import ScannerStatus
from trading_copilot.domain.trigger import LowerTimeframeTriggerSnapshot, TriggerState
from trading_copilot.persistence.models import (
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TriggerAttemptRow,
    WatchedSetupRow,
)
from trading_copilot.services.trigger_context import resolve_trigger_context
from trading_copilot.services.trigger_runtime import evaluate_current_trigger_candidate

SnapshotBuilder = Callable[[str, datetime], Awaitable[LowerTimeframeTriggerSnapshot]]


@dataclass(frozen=True)
class TriggerMonitorStatus:
    enabled: bool
    running: bool
    interval_seconds: float
    last_cycle_started_at: datetime | None
    last_cycle_completed_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_cycle_duration_ms: float | None
    armed_candidate_count: int
    eligible_candidate_count: int
    evaluation_count: int
    persisted_count: int
    confirmed_count: int
    terminal_count: int
    evaluated_symbols: list[str]
    failed_symbols: list[str]


@dataclass(frozen=True)
class _WorkItem:
    watched_setup_id: str
    setup_type: str
    arm_key: str


class TriggerMonitor:
    """Evaluate durable trigger arms without mutating scanner or exchange state."""

    def __init__(
        self,
        *,
        enabled: bool,
        interval_seconds: float,
        concurrency: int,
        sessions: async_sessionmaker[AsyncSession],
        build_snapshot: SnapshotBuilder,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.concurrency = concurrency
        self.sessions = sessions
        self.build_snapshot = build_snapshot
        self.running = False
        self.last_cycle_started_at: datetime | None = None
        self.last_cycle_completed_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.last_cycle_duration_ms: float | None = None
        self.armed_candidate_count = 0
        self.eligible_candidate_count = 0
        self.evaluation_count = 0
        self.persisted_count = 0
        self.confirmed_count = 0
        self.terminal_count = 0
        self.evaluated_symbols: list[str] = []
        self.failed_symbols: list[str] = []

    def status(self) -> TriggerMonitorStatus:
        return TriggerMonitorStatus(
            enabled=self.enabled,
            running=self.running,
            interval_seconds=self.interval_seconds,
            last_cycle_started_at=self.last_cycle_started_at,
            last_cycle_completed_at=self.last_cycle_completed_at,
            last_success_at=self.last_success_at,
            last_error=self.last_error,
            last_cycle_duration_ms=self.last_cycle_duration_ms,
            armed_candidate_count=self.armed_candidate_count,
            eligible_candidate_count=self.eligible_candidate_count,
            evaluation_count=self.evaluation_count,
            persisted_count=self.persisted_count,
            confirmed_count=self.confirmed_count,
            terminal_count=self.terminal_count,
            evaluated_symbols=list(self.evaluated_symbols),
            failed_symbols=list(self.failed_symbols),
        )

    def _reset_cycle(self, started_at: datetime) -> None:
        self.last_cycle_started_at = started_at
        self.last_error = None
        self.armed_candidate_count = 0
        self.eligible_candidate_count = 0
        self.evaluation_count = 0
        self.persisted_count = 0
        self.confirmed_count = 0
        self.terminal_count = 0
        self.evaluated_symbols = []
        self.failed_symbols = []

    async def run_cycle(self) -> None:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        self._reset_cycle(started_at)
        evaluated_symbols: set[str] = set()
        failed_symbols: set[str] = set()
        errors: list[str] = []
        terminal_success = False
        try:
            async with self.sessions() as session:
                watchlists = list(
                    await session.scalars(
                        select(ScannerWatchlistRow)
                        .where(ScannerWatchlistRow.enabled.is_(True))
                        .order_by(ScannerWatchlistRow.symbol, ScannerWatchlistRow.id)
                    )
                )
                by_symbol = {row.symbol: row for row in watchlists}
                if not by_symbol:
                    return
                candidates = list(
                    await session.scalars(
                        select(WatchedSetupRow)
                        .where(
                            WatchedSetupRow.status == ScannerStatus.TRIGGER_ARMED.value,
                            WatchedSetupRow.symbol.in_(by_symbol),
                        )
                        .order_by(WatchedSetupRow.symbol, WatchedSetupRow.setup_type)
                    )
                )
                self.armed_candidate_count = len(candidates)
                candidate_ids = [row.id for row in candidates]
                transitions = (
                    list(
                        await session.scalars(
                            select(ScannerTransitionRow).where(
                                ScannerTransitionRow.watched_setup_id.in_(candidate_ids)
                            )
                        )
                    )
                    if candidate_ids
                    else []
                )
                attempts = (
                    list(
                        await session.scalars(
                            select(TriggerAttemptRow).where(
                                TriggerAttemptRow.watched_setup_id.in_(candidate_ids)
                            )
                        )
                    )
                    if candidate_ids
                    else []
                )

            transitions_by_setup: dict[str, list[ScannerTransitionRow]] = defaultdict(list)
            for transition in transitions:
                transitions_by_setup[transition.watched_setup_id].append(transition)
            attempts_by_identity = {
                (attempt.watched_setup_id, attempt.arm_key): attempt for attempt in attempts
            }
            work_by_symbol: dict[str, list[_WorkItem]] = defaultdict(list)
            for candidate in candidates:
                try:
                    preliminary = resolve_trigger_context(
                        watched=candidate,
                        watchlist=by_symbol[candidate.symbol],
                        transitions=transitions_by_setup[candidate.id],
                    )
                except Exception as exc:  # noqa: BLE001 - isolate candidate corruption
                    failed_symbols.add(candidate.symbol)
                    errors.append(f"{candidate.symbol}/{candidate.setup_type}: {exc}")
                    continue
                if not preliminary.eligible or preliminary.arm_key is None:
                    failed_symbols.add(candidate.symbol)
                    errors.append(
                        f"{candidate.symbol}/{candidate.setup_type}: "
                        f"{preliminary.blocking_reason or 'NOT_ELIGIBLE'}"
                    )
                    continue
                attempt = attempts_by_identity.get((candidate.id, preliminary.arm_key))
                try:
                    final = resolve_trigger_context(
                        watched=candidate,
                        watchlist=by_symbol[candidate.symbol],
                        transitions=transitions_by_setup[candidate.id],
                        existing_attempt=attempt,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate candidate corruption
                    failed_symbols.add(candidate.symbol)
                    errors.append(f"{candidate.symbol}/{candidate.setup_type}: {exc}")
                    continue
                if not final.eligible:
                    failed_symbols.add(candidate.symbol)
                    errors.append(
                        f"{candidate.symbol}/{candidate.setup_type}: "
                        f"{final.blocking_reason or 'NOT_ELIGIBLE'}"
                    )
                    continue
                self.eligible_candidate_count += 1
                if attempt is not None and attempt.state == TriggerState.CONFIRMED.value:
                    self.terminal_count += 1
                    terminal_success = True
                    evaluated_symbols.add(candidate.symbol)
                    continue
                work_by_symbol[candidate.symbol].append(
                    _WorkItem(candidate.id, candidate.setup_type, final.arm_key)
                )

            semaphore = asyncio.Semaphore(self.concurrency)

            async def evaluate_symbol(symbol: str, work: list[_WorkItem]) -> None:
                async with semaphore:
                    try:
                        snapshot = await self.build_snapshot(symbol, started_at)
                    except Exception as exc:  # noqa: BLE001 - isolate symbol failure
                        failed_symbols.add(symbol)
                        errors.append(f"{symbol}: snapshot fetch failed: {exc}")
                        return
                    for item in work:
                        try:
                            async with self.sessions() as candidate_session:
                                outcome = await evaluate_current_trigger_candidate(
                                    candidate_session,
                                    watched_setup_id=item.watched_setup_id,
                                    expected_arm_key=item.arm_key,
                                    snapshot=snapshot,
                                )
                        except Exception as exc:  # noqa: BLE001 - isolate candidate failure
                            failed_symbols.add(symbol)
                            errors.append(f"{symbol}/{item.setup_type}: {exc}")
                            continue
                        self.evaluation_count += int(outcome.evaluated)
                        self.persisted_count += int(outcome.persisted)
                        self.confirmed_count += int(outcome.confirmed)
                        self.terminal_count += int(outcome.terminal)
                        if outcome.persisted or outcome.terminal:
                            evaluated_symbols.add(symbol)
                        if outcome.failure:
                            failed_symbols.add(symbol)
                            errors.append(f"{symbol}/{item.setup_type}: {outcome.failure}")

            await asyncio.gather(
                *(evaluate_symbol(symbol, work) for symbol, work in work_by_symbol.items())
            )
            self.evaluated_symbols = sorted(evaluated_symbols)
            self.failed_symbols = sorted(failed_symbols)
            if self.persisted_count or self.terminal_count or terminal_success:
                self.last_success_at = started_at
            self.last_error = "; ".join(sorted(errors)) if errors else None
        finally:
            self.evaluated_symbols = sorted(evaluated_symbols)
            self.failed_symbols = sorted(failed_symbols)
            self.last_cycle_completed_at = datetime.now(UTC)
            self.last_cycle_duration_ms = (perf_counter() - started_clock) * 1000

    async def run(self) -> None:
        if not self.enabled:
            return
        self.running = True
        try:
            while True:
                await self.run_cycle()
                await asyncio.sleep(self.interval_seconds)
        finally:
            self.running = False
