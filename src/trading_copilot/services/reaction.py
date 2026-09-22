from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from trading_copilot.services.feature_bars import FeatureBar


class ReactionState(StrEnum):
    NEUTRAL = "neutral"
    SELL_ABSORPTION = "sell_absorption"
    BUY_ABSORPTION = "buy_absorption"
    SELL_CONTINUATION = "sell_continuation"
    BUY_CONTINUATION = "buy_continuation"


@dataclass(slots=True, frozen=True)
class ReactionThresholds:
    min_total_notional: float = 25_000.0
    min_flow_imbalance: float = 0.30
    max_absorption_progress_bps: float = 3.0
    min_continuation_progress_bps: float = 5.0


@dataclass(slots=True)
class ReactionAssessment:
    state: ReactionState
    reason: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "evidence": self.evidence,
        }


def classify_reaction(
    bar: FeatureBar,
    thresholds: ReactionThresholds | None = None,
) -> ReactionAssessment:
    thresholds = thresholds or ReactionThresholds()
    move_bps = bar.mid_change_bps
    total_notional = bar.total_notional
    flow_imbalance = bar.flow_imbalance

    evidence = {
        "interval_ms": bar.interval_ms,
        "total_notional": total_notional,
        "delta_notional": bar.delta_notional,
        "flow_imbalance": flow_imbalance,
        "mid_change_bps": move_bps,
        "open_interest_delta": bar.open_interest_delta,
        "imbalance_10bps": bar.imbalance_10bps,
        "imbalance_25bps": bar.imbalance_25bps,
        "bid_replenished_qty": bar.bid_replenished_qty,
        "ask_replenished_qty": bar.ask_replenished_qty,
        "thresholds": asdict(thresholds),
    }

    if move_bps is None or total_notional < thresholds.min_total_notional:
        return ReactionAssessment(
            state=ReactionState.NEUTRAL,
            reason="Insufficient price/flow evidence for classification.",
            evidence=evidence,
        )

    sell_pressure = flow_imbalance <= -thresholds.min_flow_imbalance
    buy_pressure = flow_imbalance >= thresholds.min_flow_imbalance

    if sell_pressure:
        if move_bps <= -thresholds.min_continuation_progress_bps:
            return ReactionAssessment(
                state=ReactionState.SELL_CONTINUATION,
                reason="Aggressive selling is producing meaningful downside price progress.",
                evidence=evidence,
            )
        if move_bps >= -thresholds.max_absorption_progress_bps:
            return ReactionAssessment(
                state=ReactionState.SELL_ABSORPTION,
                reason=(
                    "Aggressive selling is not producing proportional downside price progress; "
                    "passive buyers may be absorbing flow."
                ),
                evidence=evidence,
            )

    if buy_pressure:
        if move_bps >= thresholds.min_continuation_progress_bps:
            return ReactionAssessment(
                state=ReactionState.BUY_CONTINUATION,
                reason="Aggressive buying is producing meaningful upside price progress.",
                evidence=evidence,
            )
        if move_bps <= thresholds.max_absorption_progress_bps:
            return ReactionAssessment(
                state=ReactionState.BUY_ABSORPTION,
                reason=(
                    "Aggressive buying is not producing proportional upside price progress; "
                    "passive sellers may be absorbing flow."
                ),
                evidence=evidence,
            )

    return ReactionAssessment(
        state=ReactionState.NEUTRAL,
        reason="Flow and price response do not meet a configured reaction pattern.",
        evidence=evidence,
    )
