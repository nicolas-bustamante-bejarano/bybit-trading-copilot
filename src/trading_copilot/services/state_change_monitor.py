from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_copilot.domain.account import NormalizedAccount, NormalizedPosition
from trading_copilot.domain.position_coach import PositionCoach
from trading_copilot.domain.state_change import StateChangeMonitorStatus
from trading_copilot.persistence.models import CoachStateCursorRow
from trading_copilot.services.state_change import (
    persist_closed_position,
    persist_observation,
    project_coach_state,
)

AccountFetcher = Callable[[], Awaitable[NormalizedAccount]]
CoachComposer = Callable[
    [NormalizedAccount, NormalizedPosition, AsyncSession], Awaitable[PositionCoach]
]


class StateChangeMonitor:
    def __init__(
        self,
        *,
        enabled: bool,
        interval_seconds: float,
        sessions: async_sessionmaker[AsyncSession],
        fetch_account: AccountFetcher,
        compose_coach: CoachComposer,
    ) -> None:
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.sessions = sessions
        self.fetch_account = fetch_account
        self.compose_coach = compose_coach
        self.running = False
        self.last_cycle_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None

    def status(self) -> StateChangeMonitorStatus:
        return StateChangeMonitorStatus(
            enabled=self.enabled,
            running=self.running,
            interval_seconds=self.interval_seconds,
            last_cycle_at=self.last_cycle_at,
            last_success_at=self.last_success_at,
            last_error=self.last_error,
        )

    async def run_cycle(self) -> None:
        now = datetime.now(UTC)
        self.last_cycle_at = now
        try:
            account = await self.fetch_account()
        except Exception as exc:  # noqa: BLE001 - operational boundary stays alive
            self.last_error = f"Account refresh failed: {exc}"
            return

        open_symbols = {position.symbol.upper() for position in account.positions}
        errors: list[str] = []
        for position in account.positions:
            try:
                async with self.sessions() as session:
                    coach = await self.compose_coach(account, position, session)
                    await persist_observation(
                        session,
                        symbol=position.symbol,
                        state=project_coach_state(coach),
                        coach=coach,
                        observed_at=now,
                    )
            except Exception as exc:  # noqa: BLE001 - isolate each symbol
                errors.append(f"{position.symbol}: {exc}")

        try:
            async with self.sessions() as session:
                tracked_open = list(
                    (
                        await session.scalars(
                            select(CoachStateCursorRow).where(
                                CoachStateCursorRow.position_open.is_(True)
                            )
                        )
                    ).all()
                )
                for cursor in tracked_open:
                    if cursor.symbol not in open_symbols:
                        await persist_closed_position(session, cursor.symbol, now)
        except Exception as exc:  # noqa: BLE001 - retry reconciliation next cycle
            errors.append(f"closed-position reconciliation: {exc}")

        self.last_success_at = now
        self.last_error = "; ".join(errors) if errors else None

    async def run(self) -> None:
        self.running = True
        try:
            while True:
                await self.run_cycle()
                await asyncio.sleep(self.interval_seconds)
        finally:
            self.running = False
