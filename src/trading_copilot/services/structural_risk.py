from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_copilot.domain.account import NormalizedAccount
from trading_copilot.persistence.models import TradePlanRow


def decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def structural_risk_positions(
    account: NormalizedAccount, plans_by_symbol: dict[str, TradePlanRow]
) -> list[dict[str, Any]]:
    results = []
    for position in account.positions:
        plan = plans_by_symbol.get(position.symbol)
        quantity = decimal(position.quantity) or Decimal()
        entry = decimal(position.average_entry)
        if plan is not None and plan.side.upper() == position.side.value.upper() and entry is not None:
            risk = quantity * abs(entry - plan.hard_invalidation)
            provenance = "execution_plan"
        elif position.structural_risk_usdt is not None:
            risk = decimal(position.structural_risk_usdt)
            provenance = "exchange_order" if position.stop_loss is not None else "inferred"
        else:
            risk = None
            provenance = "unknown"
        results.append(
            {
                "symbol": position.symbol,
                "correlation_group": position.correlation_group,
                "risk_usdt": risk,
                "provenance": provenance,
            }
        )
    return results


def structural_risk_summary(
    account: NormalizedAccount,
    plans_by_symbol: dict[str, TradePlanRow],
    *,
    policy_pct: Decimal | None = None,
) -> dict[str, Any]:
    equity = decimal(account.equity_usdt) or Decimal()
    policy_pct = policy_pct or decimal(account.max_group_risk_pct) or Decimal()
    groups: dict[str, dict[str, Any]] = {}
    provenance: dict[str, str] = {}
    known_total = Decimal()
    unknown: list[str] = []
    for item in structural_risk_positions(account, plans_by_symbol):
        group = groups.setdefault(
            item["correlation_group"],
            {"known_risk_usdt": Decimal(), "positions": [], "unknown": []},
        )
        group["positions"].append(item["symbol"])
        provenance[item["symbol"]] = item["provenance"]
        if item["risk_usdt"] is None:
            group["unknown"].append(item["symbol"])
            unknown.append(item["symbol"])
        else:
            group["known_risk_usdt"] += item["risk_usdt"]
            known_total += item["risk_usdt"]
    budget = equity * policy_pct
    if unknown:
        status = "INDETERMINATE"
    elif known_total > budget:
        status = "BREACH"
    else:
        status = "PASS"
    return {
        "equity_usdt": equity,
        "available_balance_usdt": decimal(account.available_balance_usdt),
        "risk_policy_pct": policy_pct,
        "risk_budget_usdt": budget,
        "known_structural_risk_usdt": known_total,
        "known_structural_risk_pct": known_total / equity if equity > 0 else None,
        "total_structural_risk_pct": None if unknown or equity <= 0 else known_total / equity,
        "risk_policy_status": status,
        "unknown_symbols": sorted(unknown),
        "provenance": provenance,
        "correlation_groups": groups,
        "positions": account.positions,
    }
