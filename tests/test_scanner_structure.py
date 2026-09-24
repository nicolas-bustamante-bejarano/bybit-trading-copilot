from decimal import Decimal

import pytest

from trading_copilot.persistence.models import (
    ChartStructureRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    TradePlanRow,
    WatchedSetupRow,
)
from trading_copilot.services.scanner_structure import (
    project_level,
    resolve_macro,
    resolve_plan_structure,
)


def plan(
    plan_id: str,
    *,
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    setup_type: str = "TREND_PULLBACK_LONG",
) -> TradePlanRow:
    return TradePlanRow(
        id=plan_id,
        symbol=symbol,
        side=side,
        setup_type=setup_type,
        thesis="test",
        hard_invalidation=Decimal(90),
        max_risk_percent=Decimal("0.01"),
    )


def watched(trade_plan_id: str | None = None) -> WatchedSetupRow:
    return WatchedSetupRow(
        id="watch-1",
        symbol="BTCUSDT",
        setup_type="TREND_PULLBACK_LONG",
        status="WATCH",
        trade_plan_id=trade_plan_id,
    )


def fib(plan_id: str) -> FibDefinitionRow:
    return FibDefinitionRow(
        id=f"fib-{plan_id}",
        trade_plan_id=plan_id,
        symbol="BTCUSDT",
        direction="LONG",
        swing_low=Decimal(100),
        swing_high=Decimal(120),
    )


def range_definition(plan_id: str) -> RangeDefinitionRow:
    return RangeDefinitionRow(
        id=f"range-{plan_id}",
        trade_plan_id=plan_id,
        symbol="BTCUSDT",
        range_low=Decimal(100),
        range_high=Decimal(120),
    )


def resolve_trend(plans, definitions, linked: str | None = None):
    return resolve_plan_structure(
        watched=watched(linked),
        plans=plans,
        definitions=definitions,
        side="LONG",
        family="TREND_PULLBACK",
    )


def resolve_range(plans, definitions, linked: str | None = None):
    item = watched(linked)
    item.setup_type = "RANGE_LONG"
    return resolve_plan_structure(
        watched=item,
        plans=plans,
        definitions=definitions,
        side="LONG",
        family="RANGE",
    )


def test_trend_explicit_compatible_linked_plan_resolves_fib():
    linked = plan("plan-1")
    result = resolve_trend([linked], [fib(linked.id)], linked.id)

    assert result.reason is None
    assert result.trade_plan_id == linked.id
    assert result.fib is not None


@pytest.mark.parametrize(
    ("changes",),
    [
        ({"symbol": "ETHUSDT"},),
        ({"side": "SHORT"},),
        ({"setup_type": "RANGE_LONG"},),
    ],
    ids=["wrong-symbol", "wrong-side", "wrong-family"],
)
def test_trend_explicit_incompatible_linked_plan_is_rejected(changes):
    linked = plan("plan-1", **changes)

    result = resolve_trend([linked], [fib(linked.id)], linked.id)

    assert result.reason == "INCOMPATIBLE_LINKED_PLAN"


def test_trend_linked_compatible_plan_without_fib_requires_structure():
    linked = plan("plan-1")

    result = resolve_trend([linked], [], linked.id)

    assert result.reason == "STRUCTURE_REQUIRED"
    assert result.trade_plan_id == linked.id


def test_trend_zero_compatible_unlinked_plans_requires_structure():
    result = resolve_trend([plan("plan-1", symbol="ETHUSDT")], [])

    assert result.reason == "STRUCTURE_REQUIRED"


def test_trend_exactly_one_compatible_unlinked_plan_resolves():
    compatible = plan("plan-1")

    result = resolve_trend([compatible], [fib(compatible.id)])

    assert result.reason is None
    assert result.fib is not None


def test_trend_multiple_compatible_unlinked_plans_are_ambiguous():
    plans = [plan("plan-1"), plan("plan-2")]

    result = resolve_trend(plans, [fib(item.id) for item in plans])

    assert result.reason == "AMBIGUOUS_STRUCTURE"


def test_trend_fib_child_with_wrong_symbol_is_rejected():
    compatible = plan("plan-1")
    stale = fib(compatible.id)
    stale.symbol = "ETHUSDT"

    result = resolve_trend([compatible], [stale])

    assert result.reason == "STRUCTURE_REQUIRED"
    assert result.trade_plan_id == compatible.id


def test_trend_fib_child_with_wrong_direction_is_rejected():
    compatible = plan("plan-1")
    stale = fib(compatible.id)
    stale.direction = "SHORT"

    result = resolve_trend([compatible], [stale])

    assert result.reason == "STRUCTURE_REQUIRED"
    assert result.trade_plan_id == compatible.id


def test_explicit_linked_plan_with_stale_child_does_not_fall_through():
    linked = plan("plan-1")
    alternative = plan("plan-2")
    stale = fib(linked.id)
    stale.symbol = "ETHUSDT"

    result = resolve_trend(
        [linked, alternative],
        [stale, fib(alternative.id)],
        linked.id,
    )

    assert result.reason == "STRUCTURE_REQUIRED"
    assert result.trade_plan_id == linked.id
    assert result.fib is None


def test_range_explicit_compatible_linked_plan_resolves_range():
    linked = plan("plan-1", setup_type="RANGE_LONG")
    result = resolve_range([linked], [range_definition(linked.id)], linked.id)

    assert result.reason is None
    assert result.trade_plan_id == linked.id
    assert result.range is not None


@pytest.mark.parametrize(
    ("changes",),
    [
        ({"symbol": "ETHUSDT", "setup_type": "RANGE_LONG"},),
        ({"side": "SHORT", "setup_type": "RANGE_LONG"},),
        ({"setup_type": "TREND_PULLBACK_LONG"},),
    ],
    ids=["wrong-symbol", "wrong-side", "wrong-family"],
)
def test_range_explicit_incompatible_linked_plan_is_rejected(changes):
    linked = plan("plan-1", **changes)

    result = resolve_range([linked], [range_definition(linked.id)], linked.id)

    assert result.reason == "INCOMPATIBLE_LINKED_PLAN"


def test_range_linked_plan_without_range_requires_structure():
    linked = plan("plan-1", setup_type="RANGE_LONG")

    result = resolve_range([linked], [], linked.id)

    assert result.reason == "STRUCTURE_REQUIRED"


def test_range_zero_compatible_unlinked_plans_requires_structure():
    result = resolve_range([plan("plan-1")], [])

    assert result.reason == "STRUCTURE_REQUIRED"


def test_range_exactly_one_compatible_unlinked_plan_resolves():
    compatible = plan("plan-1", setup_type="RANGE_LONG")

    result = resolve_range([compatible], [range_definition(compatible.id)])

    assert result.reason is None
    assert result.range is not None


def test_range_multiple_compatible_unlinked_plans_are_ambiguous():
    plans = [
        plan("plan-1", setup_type="RANGE_LONG"),
        plan("plan-2", setup_type="RANGE_LONG"),
    ]

    result = resolve_range(plans, [range_definition(item.id) for item in plans])

    assert result.reason == "AMBIGUOUS_STRUCTURE"


def test_range_child_with_wrong_symbol_is_rejected():
    compatible = plan("plan-1", setup_type="RANGE_LONG")
    stale = range_definition(compatible.id)
    stale.symbol = "ETHUSDT"

    result = resolve_range([compatible], [stale])

    assert result.reason == "STRUCTURE_REQUIRED"
    assert result.trade_plan_id == compatible.id


def structure(
    structure_id: str,
    *,
    structure_type: str = "HORIZONTAL_ZONE",
    lower: str | None = "90",
    upper: str | None = "110",
    anchor_one_time: int | None = None,
    anchor_one_price: str | None = None,
    anchor_two_time: int | None = None,
    anchor_two_price: str | None = None,
    active: bool = True,
) -> ChartStructureRow:
    return ChartStructureRow(
        id=structure_id,
        symbol="BTCUSDT",
        timeframe="4H",
        structure_type=structure_type,
        label=structure_id,
        lower_price=Decimal(lower) if lower is not None else None,
        upper_price=Decimal(upper) if upper is not None else None,
        anchor_one_time=anchor_one_time,
        anchor_one_price=Decimal(anchor_one_price) if anchor_one_price is not None else None,
        anchor_two_time=anchor_two_time,
        anchor_two_price=Decimal(anchor_two_price) if anchor_two_price is not None else None,
        active=active,
    )


def trendline(structure_id: str, **changes) -> ChartStructureRow:
    values = {
        "structure_type": "TRENDLINE",
        "lower": None,
        "upper": None,
        "anchor_one_time": 100,
        "anchor_one_price": "100",
        "anchor_two_time": 200,
        "anchor_two_price": "120",
    }
    values.update(changes)
    return structure(structure_id, **values)


def test_long_horizontal_zone_uses_upper_price():
    assert project_level(structure("zone"), 100, "LONG") == 110.0


def test_short_horizontal_zone_uses_lower_price():
    assert project_level(structure("zone"), 100, "SHORT") == 90.0


def test_trendline_interpolation_is_correct():
    assert project_level(trendline("line"), 150, "LONG") == 110.0


def test_trendline_forward_extrapolation_is_correct():
    assert project_level(trendline("line"), 250, "LONG") == 130.0


def test_macro_resolution_preserves_source_identifiable_overlay_anchors():
    result = resolve_macro(
        structures=[trendline("line")], side="LONG", timestamp=250, price=129
    )

    assert result.structure_id == "line"
    assert result.structure_metadata == {
        "symbol": "BTCUSDT",
        "timeframe": "4H",
        "lower_price": None,
        "upper_price": None,
        "anchor_one_time": 100,
        "anchor_one_price": 100.0,
        "anchor_two_time": 200,
        "anchor_two_price": 120.0,
    }


def test_trendline_equal_timestamp_anchors_are_rejected():
    assert project_level(trendline("line", anchor_two_time=100), 150, "LONG") is None


@pytest.mark.parametrize(
    "missing",
    ["anchor_one_time", "anchor_one_price", "anchor_two_time", "anchor_two_price"],
)
def test_trendline_missing_anchors_are_rejected(missing):
    assert project_level(trendline("line", **{missing: None}), 150, "LONG") is None


def test_inactive_macro_structures_are_ignored():
    result = resolve_macro(
        structures=[structure("inactive", active=False)],
        side="LONG",
        timestamp=100,
        price=110,
    )

    assert result.reason == "STRUCTURE_REQUIRED"


def test_nearest_eligible_macro_structure_wins():
    result = resolve_macro(
        structures=[structure("far", upper="140"), structure("near", upper="102")],
        side="LONG",
        timestamp=100,
        price=100,
    )

    assert result.structure_id == "near"
    assert result.breakout_level == 102.0


def test_equal_distance_macro_tie_uses_stable_structure_id_ordering():
    result = resolve_macro(
        structures=[structure("z-id", upper="110"), structure("a-id", upper="90")],
        side="LONG",
        timestamp=100,
        price=100,
    )

    assert result.structure_id == "a-id"
