# Live Microstructure Model

## Why this exists

The live layer is a confirmation and execution-quality layer for the discretionary playbook. It does not create autonomous orders and it does not replace regime/location analysis.

## Inputs

For each configured Bybit linear-perpetual symbol we consume:

1. `orderbook.50.{symbol}`
2. `publicTrade.{symbol}`
3. `tickers.{symbol}`

## Order book reconstruction

A snapshot replaces the local book. Delta entries are then applied by price:

- size `0` removes the level
- a new price inserts the level
- an existing price updates the size
- a fresh snapshot resets local state

The service rejects stale/regressive updates.

## Depth bands

At every state read, depth is calculated within 10, 25 and 50 basis points of the reconstructed midpoint.

For each band we expose:

- bid quantity
- ask quantity
- bid notional
- ask notional
- notional imbalance `(bid - ask) / (bid + ask)`

## Aggressive trade flow

Bybit's public trade side is the taker side. A `Buy` is treated as aggressive buying and a `Sell` as aggressive selling.

The 60-second window exposes quantity and notional deltas. This lets the playbook later detect cases such as heavy aggressive selling with little downside price progress.

## Book change flow

For a rolling 60-second window we aggregate:

- bid additions
- bid removals
- ask additions
- ask removals
- bid replenishment
- ask replenishment

A replenishment is size re-added at a price level shortly after size was removed from that same level.

### Important limitation

A raw L2 reduction cannot be labelled a cancellation with certainty. It can reflect cancellation, execution, or both. The production model therefore uses `removed_qty`; later inference can classify cancel-like activity by correlating the order-book event stream with executed trades.

## Normalized market state

`GET /live/{symbol}/state` provides one object for downstream playbook logic:

- last / mark / index price
- open interest
- funding rate
- spread
- 10/25/50 bps depth and imbalance
- 60-second book-change flow
- 60-second aggressive trade flow
- top ten reconstructed levels

The playbook engine should consume this normalized state rather than raw WebSocket payloads.
