from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_copilot.persistence.models import ScannerWatchlistRow
from trading_copilot.services.scanner_composer import SymbolEvaluationOutcome
from trading_copilot.services.scanner_snapshot import ScannerSymbolSnapshot

SnapshotBuilder = Callable[[str, datetime], Awaitable[ScannerSymbolSnapshot]]
SymbolComposer = Callable[
    [AsyncSession, ScannerWatchlistRow, ScannerSymbolSnapshot],
    Awaitable[SymbolEvaluationOutcome],
]


@dataclass(frozen=True)
class ScannerMonitorStatus:
    enabled: bool
    running: bool
    interval_seconds: float
    last_cycle_started_at: datetime | None
    last_cycle_completed_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_cycle_duration_ms: float | None
    watchlist_count: int
    setup_count: int
    evaluated_symbols: list[str]
    failed_symbols: list[str]


class SetupScannerMonitor:
    """One in-process scanner loop per API replica; deployment remains single-replica."""

    def __init__(
        self,
        *,
        enabled: bool,
        interval_seconds: float,
        concurrency: int,
        sessions: async_sessionmaker[AsyncSession],
        build_snapshot: SnapshotBuilder,
        compose_symbol: SymbolComposer,
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
        self.compose_symbol = compose_symbol
        self.running = False
        self.last_cycle_started_at: datetime | None = None
        self.last_cycle_completed_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.last_cycle_duration_ms: float | None = None
        self.watchlist_count = 0
        self.setup_count = 0
        self.evaluated_symbols: list[str] = []
        self.failed_symbols: list[str] = []

    def status(self) -> ScannerMonitorStatus:
        return ScannerMonitorStatus(
            enabled=self.enabled,
            running=self.running,
            interval_seconds=self.interval_seconds,
            last_cycle_started_at=self.last_cycle_started_at,
            last_cycle_completed_at=self.last_cycle_completed_at,
            last_success_at=self.last_success_at,
            last_error=self.last_error,
            last_cycle_duration_ms=self.last_cycle_duration_ms,
            watchlist_count=self.watchlist_count,
            setup_count=self.setup_count,
            evaluated_symbols=list(self.evaluated_symbols),
            failed_symbols=list(self.failed_symbols),
        )

    async def run_cycle(self) -> None:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        self.last_cycle_started_at = started_at
        self.last_error = None
        self.setup_count = 0
        self.evaluated_symbols = []
        self.failed_symbols = []
        evaluated_symbols: set[str] = set()
        failed_symbols: set[str] = set()
        errors: list[str] = []
        try:
            async with self.sessions() as session:
                rows = list(
                    await session.scalars(
                        select(ScannerWatchlistRow)
                        .where(ScannerWatchlistRow.enabled.is_(True))
                        .order_by(ScannerWatchlistRow.symbol, ScannerWatchlistRow.id)
                    )
                )
            unique: dict[str, ScannerWatchlistRow] = {}
            for row in rows:
                unique.setdefault(row.symbol, row)
            self.watchlist_count = len(unique)
            semaphore = asyncio.Semaphore(self.concurrency)

            async def evaluate_one(item: ScannerWatchlistRow) -> None:
                async with semaphore:
                    try:
                        snapshot = await self.build_snapshot(item.symbol, started_at)
                        async with self.sessions() as symbol_session:
                            outcome = await self.compose_symbol(symbol_session, item, snapshot)
                        self.setup_count += len(outcome.results)
                        if outcome.results:
                            evaluated_symbols.add(item.symbol)
                        if outcome.failed_setups:
                            failed_symbols.add(item.symbol)
                        for setup_type, error in outcome.failed_setups.items():
                            errors.append(f"{item.symbol}/{setup_type}: {error}")
                    except Exception as exc:  # noqa: BLE001 - isolate symbol failures
                        failed_symbols.add(item.symbol)
                        errors.append(f"{item.symbol}: {exc}")

            await asyncio.gather(*(evaluate_one(item) for item in unique.values()))
            self.evaluated_symbols = sorted(evaluated_symbols)
            self.failed_symbols = sorted(failed_symbols)
            if self.evaluated_symbols:
                self.last_success_at = started_at
            self.last_error = "; ".join(errors) if errors else None
        finally:
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
