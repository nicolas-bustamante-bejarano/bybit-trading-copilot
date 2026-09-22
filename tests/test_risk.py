import pytest

from trading_copilot.domain.models import OpenRiskPosition, Side
from trading_copilot.services.risk import max_position_size, portfolio_risk_summary, risk_per_unit


def test_short_risk_per_unit() -> None:
    assert risk_per_unit(Side.SHORT, 786.40, 812.0) == pytest.approx(25.6)


def test_position_size_two_percent() -> None:
    budget, per_unit, qty = max_position_size(4898.0, 0.02, Side.SHORT, 786.4, 812.0)
    assert budget == pytest.approx(97.96)
    assert per_unit == pytest.approx(25.6)
    assert qty == pytest.approx(3.8265625)


def test_portfolio_risk_matches_current_example() -> None:
    positions = [
        OpenRiskPosition(symbol="BNBUSDT", side=Side.SHORT, quantity=5.54, entry=786.4, stop=812),
        OpenRiskPosition(symbol="ETHUSDT", side=Side.SHORT, quantity=0.8, entry=2740.2, stop=2820),
    ]
    summary = portfolio_risk_summary(4898.0, positions)
    assert summary["total_risk_usdt"] == pytest.approx(205.664)
    assert summary["total_risk_pct"] == pytest.approx(205.664 / 4898.0)
    assert summary["risk_by_correlation_group"]["crypto_beta"] == pytest.approx(205.664)
