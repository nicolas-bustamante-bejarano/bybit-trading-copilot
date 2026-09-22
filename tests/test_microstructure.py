from trading_copilot.services.bybit_ws import BybitLinearStream
from trading_copilot.services.live_market import LiveMarketStore
from trading_copilot.services.orderbook import LocalOrderBook


def test_orderbook_snapshot_delta_depth_and_replenishment():
    book = LocalOrderBook(replenishment_window_ms=5_000)
    book.apply_snapshot(
        [["100", "10"], ["99", "20"]],
        [["101", "12"], ["102", "18"]],
        update_id=10,
        sequence=100,
        timestamp_ms=1_000,
    )

    assert float(book.mid_price) == 100.5

    event = book.apply_delta(
        [["100", "4"], ["98", "3"]],
        [["101", "0"], ["101.5", "8"]],
        update_id=11,
        sequence=101,
        timestamp_ms=2_000,
    )

    assert float(event.bid_removed_qty) == 6.0
    assert float(event.ask_removed_qty) == 12.0
    assert float(book.best_ask) == 101.5

    event2 = book.apply_delta(
        [["100", "9"]],
        [],
        update_id=12,
        sequence=102,
        timestamp_ms=3_000,
    )

    assert float(event2.bid_replenished_qty) == 5.0


def test_live_market_store_normalizes_messages():
    store = LiveMarketStore()
    store.handle(
        {
            "topic": "orderbook.50.BNBUSDT",
            "type": "snapshot",
            "ts": 1_000,
            "data": {
                "s": "BNBUSDT",
                "b": [["786.4", "5"]],
                "a": [["786.6", "6"]],
                "u": 1,
                "seq": 10,
            },
        }
    )
    store.handle(
        {
            "topic": "publicTrade.BNBUSDT",
            "type": "snapshot",
            "ts": 1_100,
            "data": [
                {"T": 1_100, "s": "BNBUSDT", "S": "Sell", "v": "2", "p": "786.4"},
                {"T": 1_200, "s": "BNBUSDT", "S": "Buy", "v": "1", "p": "786.6"},
            ],
        }
    )
    store.handle(
        {
            "topic": "tickers.BNBUSDT",
            "type": "snapshot",
            "ts": 1_200,
            "data": {
                "symbol": "BNBUSDT",
                "lastPrice": "786.5",
                "markPrice": "786.45",
                "openInterest": "10000",
                "fundingRate": "-0.00003",
            },
        }
    )

    state = store.state("BNBUSDT", now_ms=1_500)

    assert state["last_price"] == 786.5
    assert state["open_interest"] == 10000.0
    assert state["trade_flow_60s"]["delta_qty"] == -1.0
    assert state["orderbook"]["best_bid"] == 786.4


def test_stream_subscribes_to_expected_topics():
    stream = BybitLinearStream(["ethusdt", "BNBUSDT"])

    assert "orderbook.50.BNBUSDT" in stream.subscription_args
    assert "publicTrade.ETHUSDT" in stream.subscription_args
    assert "tickers.BNBUSDT" in stream.subscription_args
    assert len(stream.subscription_args) == 6
