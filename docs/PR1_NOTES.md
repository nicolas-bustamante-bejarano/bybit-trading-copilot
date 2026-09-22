# Milestone 1 implementation notes

This branch implements the first live Bybit market-intelligence layer for the trading copilot.

## Included

- Bybit public linear WebSocket client with reconnect/backoff and resubscription
- L2 snapshot + delta order-book reconstruction
- Sequence/update-id validation
- 10/25/50 bps depth and notional depth
- Spread and order-book imbalance
- Bid/ask add/remove/replenishment tracking
- Rolling public trade-flow aggregation
- Live normalized market-state service
- REST enrichment for funding and open interest
- FastAPI endpoints for live state lifecycle
- Deterministic microstructure tests
- CI lint + test workflow

## Trading-system intent

The live microstructure layer is confirmation data only. It does not generate or place orders. The product workflow remains:

`regime -> location -> reaction -> execution -> review`

Liquidity and order flow are evaluated most heavily when price reaches a meaningful Fib/range/HTF location.

## Next milestone

Wire the live market-state object into the playbook state machine:

- Trend pullback long/short
- Range long/short
- Setup states: `NO_SETUP -> APPROACHING_LOCATION -> CONFIRMATION_DEVELOPING -> WAITING_FOR_TRIGGER -> READY`
- Explicit correlated portfolio-risk gate before any manual execution decision
