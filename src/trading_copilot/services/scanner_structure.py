from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from trading_copilot.persistence.models import (
    ChartStructureRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    TradePlanRow,
    WatchedSetupRow,
)


@dataclass(frozen=True)
class StructureResolution:
    reason: str | None = None
    trade_plan_id: str | None = None
    fib: FibDefinitionRow | None = None
    range: RangeDefinitionRow | None = None
    structure_id: str | None = None
    structure_label: str | None = None
    structure_type: str | None = None
    breakout_level: float | None = None


def _compatible(plan: TradePlanRow, symbol: str, side: str, family: str) -> bool:
    normalized_side = side.upper()
    normalized_family = family.upper()
    return (
        plan.symbol == symbol.upper()
        and plan.side.upper() == normalized_side
        and plan.setup_type.upper() in {normalized_family, f"{normalized_family}_{normalized_side}"}
    )


def resolve_plan_structure(
    *,
    watched: WatchedSetupRow,
    plans: Iterable[TradePlanRow],
    definitions: Iterable[FibDefinitionRow | RangeDefinitionRow],
    side: str,
    family: str,
) -> StructureResolution:
    plan_rows = list(plans)
    definition_type = FibDefinitionRow if family.upper() == "TREND_PULLBACK" else RangeDefinitionRow
    defs = [definition for definition in definitions if isinstance(definition, definition_type)]
    selected = [plan for plan in plan_rows if _compatible(plan, watched.symbol, side, family)]
    if watched.trade_plan_id:
        linked = next((plan for plan in plan_rows if plan.id == watched.trade_plan_id), None)
        if linked is None or not _compatible(linked, watched.symbol, side, family):
            return StructureResolution(reason="INCOMPATIBLE_LINKED_PLAN")
        selected = [linked]
    if not selected:
        return StructureResolution(reason="STRUCTURE_REQUIRED")
    if not watched.trade_plan_id and len(selected) != 1:
        return StructureResolution(reason="AMBIGUOUS_STRUCTURE")
    plan = selected[0]
    found = [
        item
        for item in defs
        if item.trade_plan_id == plan.id
        and item.symbol == plan.symbol
        and (
            not isinstance(item, FibDefinitionRow)
            or item.direction.upper() == side.upper()
        )
    ]
    if len(found) != 1:
        return StructureResolution(reason="STRUCTURE_REQUIRED", trade_plan_id=plan.id)
    value = found[0]
    return StructureResolution(
        trade_plan_id=plan.id,
        fib=value if isinstance(value, FibDefinitionRow) else None,
        range=value if isinstance(value, RangeDefinitionRow) else None,
    )


def project_level(structure: ChartStructureRow, timestamp: int, side: str) -> float | None:
    if structure.structure_type == "HORIZONTAL_ZONE":
        price = structure.upper_price if side.upper() == "LONG" else structure.lower_price
        return float(price) if price and price > 0 else None
    values = (
        structure.anchor_one_time,
        structure.anchor_one_price,
        structure.anchor_two_time,
        structure.anchor_two_price,
    )
    if structure.structure_type != "TRENDLINE" or any(value is None for value in values):
        return None
    t1, p1, t2, p2 = values
    if t1 == t2 or p1 <= 0 or p2 <= 0:
        return None
    return float(p1 + (p2 - p1) * Decimal(timestamp - t1) / Decimal(t2 - t1))


def resolve_macro(
    *,
    structures: Iterable[ChartStructureRow],
    side: str,
    timestamp: int,
    price: float,
    pinned_structure_id: str | None = None,
) -> StructureResolution:
    choices = [(row, project_level(row, timestamp, side)) for row in structures if row.active]
    choices = [(row, level) for row, level in choices if level is not None]
    if pinned_structure_id:
        choices = [(row, level) for row, level in choices if row.id == pinned_structure_id]
    if not choices:
        return StructureResolution(reason="STRUCTURE_REQUIRED")
    row, level = min(choices, key=lambda item: (abs(price - item[1]), item[0].id))
    return StructureResolution(structure_id=row.id, structure_label=row.label, structure_type=row.structure_type, breakout_level=level)
