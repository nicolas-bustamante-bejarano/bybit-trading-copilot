import pytest

from trading_copilot.services.account_normalizer import (
    normalize_account_snapshot,
    portfolio_live_view,
)


def _snapshot() -> dict:
    return {
        "positions": [
            {
                "symbol": "BNBUSDT",
                "side": "Sell",
                "size": "5.54",
                "avgPrice": "786.40",
                "markPrice": "784.90",
                "leverage": "10",
                "liqPrice": "855.0",
                "unrealisedPnl": "8.31",
                "stopLoss": "812",
                "takeProfit": "769",
            },
            {
                "symbol": "ETHUSDT",
                "side": "Sell",
                "size": "0.80",
                "avgPrice": "2740.20",
                "markPrice": "2738.50",
                "leverage": "10",
                "liqPrice": "3000",
                "unrealisedPnl": "1.36",
                "stopLoss": "0",
                "takeProfit": "0",
            },
        ],
        "open_orders": [
            {
                "orderId": "eth-stop",
                "symbol": "ETHUSDT",
                "side": "Buy",
                "orderType": "Market",
                "qty": "0.80",
                "leavesQty": "0.80",
                "triggerPrice": "2820",
                "price": "0",
                "reduceOnly": True,
                "orderStatus": "Untriggered",
            },
            {
                "orderId": "eth-tp",
                "symbol": "ETHUSDT",
                "side": "Buy",
                "orderType": "Market",
                "qty": "0.40",
                "leavesQty": "0.40",
                "triggerPrice": "2685",
                "price": "0",
                "reduceOnly": True,
                "orderStatus": "Untriggered",
            },
        ],
        "executions": [
            {
                "execId": "fill-1",
                "orderId": "order-1",
                "symbol": "BNBUSDT",
                "side": "Sell",
                "execPrice": "786.40",
                "execQty": "5.54",
                "execTime": "1790070000000",
                "execFee": "0.25",
            }
        ],
        "wallet": [
            {
                "totalEquity": "4898",
                "totalAvailableBalance": "3100",
            }
        ],
    }


def test_normalizes_current_positions_and_structural_risk() -> None:
    account = normalize_account_snapshot(_snapshot(), max_group_risk_pct=0.02)

    assert account.equity_usdt == 4898
    assert account.risk_budget_usdt == pytest.approx(97.96)
    assert account.total_structural_risk_usdt == pytest.approx(205.664)
    assert account.total_structural_risk_pct == pytest.approx(205.664 / 4898)
    assert account.within_risk_budget is False
    assert account.unprotected_symbols == []

    positions = {position.symbol: position for position in account.positions}
    assert positions["BNBUSDT"].stop_loss == 812
    assert positions["BNBUSDT"].structural_risk_usdt == pytest.approx(141.824)
    assert positions["ETHUSDT"].stop_loss == 2820
    assert positions["ETHUSDT"].take_profit == 2685
    assert positions["ETHUSDT"].structural_risk_usdt == pytest.approx(63.84)


def test_marks_position_unprotected_when_no_valid_stop_exists() -> None:
    snapshot = _snapshot()
    snapshot["positions"] = [snapshot["positions"][1]]
    snapshot["open_orders"] = []

    account = normalize_account_snapshot(snapshot)

    assert account.unprotected_symbols == ["ETHUSDT"]
    assert account.positions[0].protected is False
    assert account.positions[0].structural_risk_usdt is None
    assert account.within_risk_budget is False


def test_portfolio_live_groups_crypto_beta_exposure() -> None:
    account = normalize_account_snapshot(_snapshot())
    view = portfolio_live_view(account)

    assert view["correlation_groups"]["crypto_beta"]["positions"] == ["BNBUSDT", "ETHUSDT"]
    assert view["correlation_groups"]["crypto_beta"]["risk_usdt"] == pytest.approx(205.664)
    assert view["within_policy"] is False
