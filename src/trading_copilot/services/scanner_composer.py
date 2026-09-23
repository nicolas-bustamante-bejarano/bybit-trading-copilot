from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.scanner import (
    ScannerResult,
    ScannerStatus,
    enabled_setup_types,
)
from trading_copilot.persistence.models import (
    ChartStructureRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    ScannerWatchlistRow,
    TradePlanRow,
    WatchedSetupRow,
)
from trading_copilot.services.scanner_macro import evaluate_macro_breakout
from trading_copilot.services.scanner_persistence import persist_candidate
from trading_copilot.services.scanner_playbook import evaluate_scanner_playbook
from trading_copilot.services.scanner_snapshot import ScannerSymbolSnapshot
from trading_copilot.services.scanner_structure import resolve_macro

ACCEPTED_MACRO_STATUSES = {
    ScannerStatus.BREAKOUT_ACCEPTED.value,
    ScannerStatus.RETEST_PENDING.value,
    ScannerStatus.TRIGGER_ARMED.value,
}


@dataclass(frozen=True)
class SymbolEvaluationOutcome:
    results: list[ScannerResult] = field(default_factory=list)
    failed_setups: dict[str, str] = field(default_factory=dict)


async def evaluate_symbol(
    session: AsyncSession,
    watchlist_item: ScannerWatchlistRow,
    snapshot: ScannerSymbolSnapshot,
) -> SymbolEvaluationOutcome:
    if snapshot.symbol != watchlist_item.symbol:
        raise ValueError("snapshot symbol does not match watchlist symbol")
    setup_types = enabled_setup_types(watchlist_item.enabled_playbooks)
    if not setup_types:
        return SymbolEvaluationOutcome()

    plans = list(
        await session.scalars(
            select(TradePlanRow).where(
                TradePlanRow.symbol == snapshot.symbol,
                TradePlanRow.lifecycle_status == "ACTIVE",
            )
        )
    )
    fibs = list(
        await session.scalars(
            select(FibDefinitionRow).where(FibDefinitionRow.symbol == snapshot.symbol)
        )
    )
    ranges = list(
        await session.scalars(
            select(RangeDefinitionRow).where(RangeDefinitionRow.symbol == snapshot.symbol)
        )
    )
    structures = list(
        await session.scalars(
            select(ChartStructureRow).where(ChartStructureRow.symbol == snapshot.symbol)
        )
    )
    current_rows = {
        row.setup_type: row
        for row in await session.scalars(
            select(WatchedSetupRow).where(
                WatchedSetupRow.symbol == snapshot.symbol,
                WatchedSetupRow.setup_type.in_([setup.value for setup in setup_types]),
            )
        )
    }

    results: list[ScannerResult] = []
    failed: dict[str, str] = {}
    for setup_type in setup_types:
        existing = current_rows.get(setup_type.value)
        watched = existing or WatchedSetupRow(
            symbol=snapshot.symbol,
            setup_type=setup_type.value,
            status=ScannerStatus.WATCH.value,
            state={},
        )
        try:
            if setup_type.value.startswith("MACRO_BREAKOUT"):
                result, state = _evaluate_macro(
                    watched=watched,
                    snapshot=snapshot,
                    watchlist_item=watchlist_item,
                    structures=structures,
                )
            else:
                result = evaluate_scanner_playbook(
                    watched=watched,
                    plans=plans,
                    definitions=[*fibs, *ranges],
                    price=snapshot.current_price,
                    regime_1h=snapshot.one_hour.regime,
                    regime_4h=snapshot.four_hour.regime,
                    stoch_k=snapshot.one_hour.stoch_k,
                    stoch_d=snapshot.one_hour.stoch_d,
                    prev_stoch_k=snapshot.one_hour.prev_stoch_k,
                    prev_stoch_d=snapshot.one_hour.prev_stoch_d,
                    reaction_state=snapshot.reaction_state,
                    approach_tolerance_bps=float(watchlist_item.approach_tolerance_bps),
                    data_status=snapshot.data_status,
                    evaluated_at=snapshot.evaluated_at,
                )
                state = result.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - pure evaluator failure is setup-local
            failed[setup_type.value] = str(exc)
            continue
        try:
            await persist_candidate(
                session,
                symbol=snapshot.symbol,
                setup_type=setup_type.value,
                status=result.status.value,
                state=state,
                trade_plan_id=result.linked_trade_plan_id,
            )
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - persistence failure ends this symbol safely
            await session.rollback()
            failed[setup_type.value] = str(exc)
            break
    return SymbolEvaluationOutcome(results=results, failed_setups=failed)


def _evaluate_macro(
    *,
    watched: WatchedSetupRow,
    snapshot: ScannerSymbolSnapshot,
    watchlist_item: ScannerWatchlistRow,
    structures: list[ChartStructureRow],
) -> tuple[ScannerResult, dict[str, Any]]:
    side = "LONG" if watched.setup_type.endswith("_LONG") else "SHORT"
    prior = dict(watched.state or {})
    accepted_reference = _accepted_macro_reference(watched, side)
    pinned_structure_id = accepted_reference[0] if accepted_reference is not None else None
    resolution = resolve_macro(
        structures=structures,
        side=side,
        timestamp=int(snapshot.evaluated_at.timestamp() * 1000),
        price=snapshot.current_price,
        pinned_structure_id=pinned_structure_id,
    )
    if resolution.reason:
        if accepted_reference is not None:
            raise RuntimeError("ACCEPTED_STRUCTURE_UNAVAILABLE")
        result = ScannerResult(
            symbol=snapshot.symbol,
            setup_type=watched.setup_type,
            side=side,
            status=ScannerStatus.WATCH,
            price=snapshot.current_price,
            evaluated_at=snapshot.evaluated_at,
            blocking_reasons=[resolution.reason],
            next_conditions=["define actionable breakout structure"],
            data_status=snapshot.data_status,
            linked_trade_plan_id=watched.trade_plan_id,
        )
        state = _macro_state_payload(
            result=result,
            lifecycle_state={},
            structure_id=None,
            structure_label=None,
            structure_type=None,
            breakout_level=None,
            distance_bps=None,
            qualifying_close_count=0,
            required_acceptance_bars=watchlist_item.acceptance_bars,
        )
        return result, state

    lifecycle_level = (
        accepted_reference[1]
        if accepted_reference is not None
        else resolution.breakout_level
    )
    macro = evaluate_macro_breakout(
        side=side,
        current_price=snapshot.current_price,
        breakout_level=lifecycle_level,
        approach_tolerance_bps=float(watchlist_item.approach_tolerance_bps),
        retest_tolerance_bps=float(watchlist_item.retest_tolerance_bps),
        acceptance_bars=watchlist_item.acceptance_bars,
        completed_1h_closes=snapshot.completed_1h_closes,
        previous_status=watched.status if watched.id is not None else None,
        previous_state=prior,
        structure_id=resolution.structure_id,
        structure_metadata={
            "label": resolution.structure_label,
            "type": resolution.structure_type,
        },
        evaluated_at=snapshot.evaluated_at,
    )
    result = ScannerResult(
        symbol=snapshot.symbol,
        setup_type=watched.setup_type,
        side=side,
        status=macro.status,
        price=snapshot.current_price,
        evaluated_at=snapshot.evaluated_at,
        structure={
            "id": macro.structure_id,
            "breakout_level": macro.breakout_level,
            **macro.structure,
        },
        conditions=[
            {
                "qualifying_close_count": macro.qualifying_close_count,
                "required_acceptance_bars": macro.required_acceptance_bars,
            }
        ],
        blocking_reasons=macro.blocking_reasons,
        next_conditions=macro.next_conditions,
        data_status=snapshot.data_status,
        linked_trade_plan_id=watched.trade_plan_id,
    )
    state = _macro_state_payload(
        result=result,
        lifecycle_state=macro.state,
        structure_id=macro.structure_id,
        structure_label=resolution.structure_label,
        structure_type=resolution.structure_type,
        breakout_level=macro.breakout_level,
        distance_bps=macro.distance_bps,
        qualifying_close_count=macro.qualifying_close_count,
        required_acceptance_bars=macro.required_acceptance_bars,
    )
    return result, state


def _macro_state_payload(
    *,
    result: ScannerResult,
    lifecycle_state: dict[str, Any],
    structure_id: str | None,
    structure_label: str | None,
    structure_type: str | None,
    breakout_level: float | None,
    distance_bps: float | None,
    qualifying_close_count: int,
    required_acceptance_bars: int,
) -> dict[str, Any]:
    return {
        **lifecycle_state,
        "symbol": result.symbol,
        "setup_type": result.setup_type.value,
        "side": result.side,
        "status": result.status.value,
        "price": result.price,
        "evaluated_at": result.evaluated_at.isoformat(),
        "structure": {
            "structure_id": structure_id,
            "label": structure_label,
            "type": structure_type,
            "breakout_level": breakout_level,
        },
        "distance_bps": distance_bps,
        "qualifying_close_count": qualifying_close_count,
        "required_acceptance_bars": required_acceptance_bars,
        "blocking_reasons": list(result.blocking_reasons),
        "next_conditions": list(result.next_conditions),
        "data_status": result.data_status.value,
    }


def _accepted_macro_reference(
    watched: WatchedSetupRow, side: str
) -> tuple[str, float] | None:
    if watched.status not in ACCEPTED_MACRO_STATUSES:
        return None
    prior = watched.state or {}
    structure_id = prior.get("accepted_structure_id")
    level = prior.get("accepted_breakout_level")
    accepted_side = prior.get("accepted_side")
    if (
        not isinstance(structure_id, str)
        or not structure_id
        or isinstance(level, bool)
        or not isinstance(level, (int, float))
        or not isfinite(float(level))
        or float(level) <= 0
        or accepted_side != side
    ):
        return None
    return structure_id, float(level)
