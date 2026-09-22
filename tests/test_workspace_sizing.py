from decimal import Decimal

from trading_copilot.domain.workspace import SizingRequest, SizingStage
from trading_copilot.services.sizing import build_sizing_plan


def plan(**kwargs):
    request = SizingRequest(
        symbol="bnbusdt", side="LONG", entry="100", hard_invalidation="95", max_risk_percent="0.01",
        stages=[SizingStage(name="PROBE", allocation="0.5", required_evidence=["LOCATION_VALID"]), SizingStage(name="ADD_1", allocation="0.5", required_evidence=["BUYER_CONFIRMATION"])],
        current_evidence=["LOCATION_VALID"], **kwargs
    )
    return build_sizing_plan(request, equity=Decimal(10000), available_margin=Decimal(500), group_risk=Decimal(20), group_unknown=False, qty_step=Decimal("0.1"), min_qty=Decimal("0.1"), min_notional=Decimal(5))


def test_sizing_uses_stop_and_friction_and_leverage_does_not_change_quantity():
    low = plan(requested_leverage="2")
    high = plan(requested_leverage="20")
    assert low["maximum_quantity"] == high["maximum_quantity"]
    assert low["risk_per_unit_usdt"] > Decimal(5)
    assert high["estimated_margin_usdt"] < low["estimated_margin_usdt"]


def test_add_needs_novel_explicit_evidence_and_adverse_price_is_not_an_input():
    result = plan()
    assert result["stages"][0]["allowed"] is True
    assert result["stages"][1]["allowed"] is False
    assert "Missing required evidence: BUYER_CONFIRMATION" in result["stages"][1]["reasons"]


def test_indeterminate_group_locks_all_stages():
    request = SizingRequest(symbol="ETHUSDT", side="SHORT", entry="100", hard_invalidation="105", max_risk_percent="0.01")
    result = build_sizing_plan(request, equity=Decimal(10000), available_margin=None, group_risk=Decimal(), group_unknown=True, qty_step=Decimal(1), min_qty=Decimal(1), min_notional=Decimal(5))
    assert result["stages"][0]["status"] == "INDETERMINATE"


def test_breached_group_has_zero_permitted_risk_and_conservative_stage_totals():
    request = SizingRequest(
        symbol="BNBUSDT", side="LONG", entry="100", hard_invalidation="95", max_risk_percent="0.01",
        correlation_group="crypto_beta",
        stages=[SizingStage(name="PROBE", allocation="0.4"), SizingStage(name="ADD_1", allocation="0.6")],
    )
    result = build_sizing_plan(request, equity=Decimal(10000), available_margin=Decimal(500), group_risk=Decimal(110), group_unknown=False, qty_step=Decimal("0.1"), min_qty=Decimal("0.1"), min_notional=Decimal(5))
    assert result["permitted_risk_usdt"] == 0
    assert all(not stage["allowed"] for stage in result["stages"])
    assert result["stages"][-1]["cumulative_risk_usdt"] <= result["permitted_risk_usdt"]


def test_add_requires_recorded_prior_stage_and_trusted_baseline_then_new_evidence():
    request = SizingRequest(
        symbol="ETHUSDT", side="LONG", entry="100", hard_invalidation="95", max_risk_percent="0.01",
        stages=[SizingStage(name="PROBE", allocation="0.5", required_evidence=["LOCATION"]), SizingStage(name="ADD_1", allocation="0.5", required_evidence=["BUYER_CONFIRMATION"])],
        current_evidence=["LOCATION", "BUYER_CONFIRMATION"], prior_evidence=["LOCATION"],
    )
    locked = build_sizing_plan(request, equity=Decimal(10000), available_margin=Decimal(500), group_risk=Decimal(), group_unknown=False, qty_step=Decimal("0.1"), min_qty=Decimal("0.1"), min_notional=Decimal(5))
    assert locked["stages"][1]["allowed"] is False
    unlocked_request = request.model_copy(update={"completed_stage_count": 1, "prior_stage_baseline_trusted": True})
    unlocked = build_sizing_plan(unlocked_request, equity=Decimal(10000), available_margin=Decimal(500), group_risk=Decimal(), group_unknown=False, qty_step=Decimal("0.1"), min_qty=Decimal("0.1"), min_notional=Decimal(5))
    assert unlocked["stages"][1]["allowed"] is True
