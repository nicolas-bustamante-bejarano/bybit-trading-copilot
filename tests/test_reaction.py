import pytest

from trading_copilot.services.feature_bars import FeatureBar, FeatureBarAggregator
from trading_copilot.services.orderbook import LocalOrderBook
from trading_copilot.services.reaction import ReactionState, classify_reaction


def make_bar(
    *,
    mid_open: float,
    mid_close: float,
    buy_notional: float,
    sell_notional: float,
) -> FeatureBar:
    return FeatureBar(
        symbol="ETHUSDT",
        interval_ms=60_000,
        start_ms=0,
        end_ms=60_000,
        mid_open=mid_open,
        mid_high=max(mid_open, mid_close),
        mid_low=min(mid_open, mid_close),
        mid_close=mid_close,
        buy_notional=buy_notional,
        sell_notional=sell_notional,
    )


@pytest.mark.parametrize(
    ("bar", "expected"),
    [
        (
            make_bar(
                mid_open=100.0,
                mid_close=99.99,
                buy_notional=20_000,
                sell_notional=80_000,
            ),
            ReactionState.SELL_ABSORPTION,
        ),
        (
            make_bar(
                mid_open=100.0,
                mid_close=100.01,
                buy_notional=80_000,
                sell_notional=20_000,
            ),
            ReactionState.BUY_ABSORPTION,
        ),
        (
            make_bar(
                mid_open=100.0,
                mid_close=99.90,
                buy_notional=20_000,
                sell_notional=80_000,
            ),
            ReactionState.SELL_CONTINUATION,
        ),
        (
            make_bar(
                mid_open=100.0,
                mid_close=100.10,
                buy_notional=80_000,
                sell_notional=20_000,
            ),
            ReactionState.BUY_CONTINUATION,
        ),
    ],
)
def test_reaction_classification(bar: FeatureBar, expected: ReactionState):
    assessment = classify_reaction(bar)

    assert assessment.state == expected
    assert assessment.evidence["total_notional"] == 100_000


def test_reaction_neutral_when_flow_is_too_small():
    bar = make_bar(
        mid_open=100.0,
        mid_close=99.99,
        buy_notional=1_000,
        sell_notional=4_000,
    )

    assert classify_reaction(bar).state == ReactionState.NEUTRAL


def test_feature_bar_rolls_and_calculates_flow_price_and_oi():
    aggregator = FeatureBarAggregator("BNBUSDT", interval_ms=1_000)
    book = LocalOrderBook()
    book.apply_snapshot(
        [["99.9", "100"]],
        [["100.1", "100"]],
        update_id=1,
        sequence=1,
        timestamp_ms=100,
    )
    aggregator.on_book(100, book)
    aggregator.on_ticker(150, open_interest=1_000, funding_rate=0.0001)
    aggregator.on_trade(200, "Sell", 100.0, 600)
    aggregator.on_trade(300, "Buy", 100.0, 100)

    book.apply_delta(
        [["99.9", "90"], ["99.95", "20"]],
        [["100.1", "100"]],
        update_id=2,
        sequence=2,
        timestamp_ms=800,
    )
    aggregator.on_book(800, book)
    aggregator.on_ticker(900, open_interest=1_010, funding_rate=0.0001)
    aggregator.on_trade(1_100, "Sell", 100.0, 1)

    completed = aggregator.latest_completed()

    assert completed is not None
    assert completed.total_notional == 70_000
    assert completed.delta_notional == -50_000
    assert completed.open_interest_delta == 10
    assert completed.mid_change_bps is not None
    assert aggregator.current_snapshot() is not None


def test_late_feature_event_is_counted_not_raised():
    aggregator = FeatureBarAggregator("BNBUSDT", interval_ms=1_000)
    aggregator.on_trade(2_100, "Buy", 100.0, 1)
    aggregator.on_trade(1_900, "Sell", 100.0, 1)

    assert aggregator.late_event_count == 1
