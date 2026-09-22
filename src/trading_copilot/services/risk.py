from trading_copilot.domain.models import OpenRiskPosition, Side


def risk_per_unit(side: Side, entry: float, stop: float) -> float:
    if side == Side.LONG:
        if stop >= entry:
            raise ValueError("Long stop must be below entry")
        return entry - stop
    if stop <= entry:
        raise ValueError("Short stop must be above entry")
    return stop - entry


def max_position_size(
    account_equity: float,
    max_risk_pct: float,
    side: Side,
    entry: float,
    stop: float,
) -> tuple[float, float, float]:
    budget = account_equity * max_risk_pct
    per_unit = risk_per_unit(side, entry, stop)
    return budget, per_unit, budget / per_unit


def position_risk(position: OpenRiskPosition) -> float:
    return position.quantity * risk_per_unit(position.side, position.entry, position.stop)


def portfolio_risk_summary(
    account_equity: float,
    positions: list[OpenRiskPosition],
) -> dict:
    rows = []
    by_group: dict[str, float] = {}
    total = 0.0

    for p in positions:
        risk = position_risk(p)
        total += risk
        by_group[p.correlation_group] = by_group.get(p.correlation_group, 0.0) + risk
        rows.append(
            {
                "symbol": p.symbol,
                "correlation_group": p.correlation_group,
                "risk_usdt": risk,
                "risk_pct": risk / account_equity,
            }
        )

    return {
        "positions": rows,
        "total_risk_usdt": total,
        "total_risk_pct": total / account_equity,
        "risk_by_correlation_group": by_group,
    }
