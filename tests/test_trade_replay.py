from decimal import Decimal

import pytest

from trading_copilot.services.trade_replay import (
    Candle,
    build_entry_replay_review,
    excursion_metrics,
    regime_at_entry,
)

MINUTE = 60_000


def _candles(closes: list[float], interval_ms: int) -> list[Candle]:
    rows: list[Candle] = []
    for index, close in enumerate(closes):
        rows.append(
            Candle(
                start_ms=index * interval_ms,
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1,
            )
        )
    return rows


def test_regime_uses_only_bars_closed_before_entry():
    interval_ms = 15 * MINUTE
    pre_entry = _candles([120 - index for index in range(21)], interval_ms)
    entry_bar = Candle(
        start_ms=21 * interval_ms,
        open=99,
        high=160,
        low=98,
        close=155,
        volume=100,
    )
    entry_ms = entry_bar.start_ms + 5 * MINUTE

    regime = regime_at_entry(pre_entry + [entry_bar], entry_ms, interval_ms, "LONG")

    assert regime["status"] == "BEARISH"
    assert regime["aligned"] is False
    assert regime["closed_bars_used"] == 21


def test_excursion_metrics_exclude_ambiguous_entry_candle():
    interval_ms = 15 * MINUTE
    candles = [
        Candle(0, 100, 120, 80, 100, 1),
        Candle(interval_ms, 100, 110, 96, 108, 1),
        Candle(2 * interval_ms, 108, 109, 101, 102, 1),
    ]

    metrics = excursion_metrics(
        candles,
        side="LONG",
        entry_price=100,
        invalidation=95,
        entry_timestamp_ms=5 * MINUTE,
        interval_ms=interval_ms,
    )

    assert metrics["mfe_r"] == pytest.approx(2.0)
    assert metrics["mae_r"] == pytest.approx(0.8)
    assert metrics["entry_candle_excluded"] is True
    assert metrics["invalidation_touched"] is False


def test_unconfirmed_same_session_flip_is_wait_and_d_grade():
    candles_15m = _candles([130 - index for index in range(30)], 15 * MINUTE)
    candles_1h = _candles([200 - index for index in range(30)], 60 * MINUTE)
    entry_ms = 31 * 60 * MINUTE

    review = build_entry_replay_review(
        trade_plan_id="btc-long",
        symbol="BTCUSDT",
        side="LONG",
        setup_type="RANGE_LONG",
        entry_timestamp_ms=entry_ms,
        entry_price=Decimal("85059"),
        quantity=Decimal("0.05"),
        invalidation=Decimal("84129"),
        max_risk_percent=Decimal("0.01"),
        planned=True,
        confirmation_satisfied=False,
        risk_policy_satisfied=True,
        evidence_present=["AT_LOCATION_SUPPORT"],
        evidence_missing=["15m reclaim"],
        confirmation_conditions=[{"description": "15m reclaim and successful retest"}],
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        post_end_timestamp_ms=entry_ms + 2 * 60 * MINUTE,
        thesis_flip=True,
        prior_trade={"symbol": "ETHUSDT", "side": "SHORT", "event_type": "EXIT"},
    )

    diagnostics = review["entry_diagnostics"]
    assert diagnostics["decision_at_entry"] == "WAIT"
    assert diagnostics["grade"] == "D"
    assert "ENTRY_WITHOUT_CONFIRMATION" in diagnostics["flags"]
    assert "SAME_SESSION_THESIS_FLIP" in diagnostics["flags"]
    assert "LOWER_TIMEFRAME_TREND_MISALIGNMENT" in diagnostics["flags"]
    assert diagnostics["required_before_entry"] == [
        "15m reclaim and successful retest",
        "Wait for 15m regime/structure to align or reclaim.",
        "Require fresh confirmation after an opposite-direction exit.",
    ]


def test_fully_confirmed_aligned_entry_can_grade_a():
    candles_15m = _candles([100 + index for index in range(30)], 15 * MINUTE)
    candles_1h = _candles([100 + index for index in range(30)], 60 * MINUTE)
    entry_ms = 31 * 60 * MINUTE

    review = build_entry_replay_review(
        trade_plan_id="btc-long",
        symbol="BTCUSDT",
        side="LONG",
        setup_type="TREND_PULLBACK",
        entry_timestamp_ms=entry_ms,
        entry_price=100,
        quantity=1,
        invalidation=95,
        max_risk_percent=Decimal("0.01"),
        planned=True,
        confirmation_satisfied=True,
        risk_policy_satisfied=True,
        evidence_present=["AT_LOCATION_SUPPORT", "RECLAIM_CONFIRMED"],
        evidence_missing=[],
        confirmation_conditions=[],
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        post_end_timestamp_ms=entry_ms + 60 * MINUTE,
    )

    assert review["entry_diagnostics"]["decision_at_entry"] == "ENTER"
    assert review["entry_diagnostics"]["grade"] == "A"
