# PR 1 implementation notes

This branch establishes the in-memory live market-intelligence spine.

Implemented:

- Bybit public linear WebSocket collector with reconnect/backoff
- L2 snapshot + delta reconstruction at 50 levels
- 10/25/50 bps quantity and notional depth
- notional order-book imbalance
- 60-second aggressive taker buy/sell flow
- 60-second book add/remove flow
- near-term same-level replenishment detection
- live ticker normalization including funding and open interest
- historical open-interest REST method
- FastAPI live state endpoints
- deterministic unit tests
- CI workflow

Still intentionally deferred:

- persistence of 1s/1m feature bars
- absorption / failed-auction classifier
- cross-venue data
- private Bybit account integration
- any order placement or cancellation

The next implementation slice should persist normalized feature bars and calculate flow-vs-price response so the playbook engine can reason about absorption at Fib/range locations.
