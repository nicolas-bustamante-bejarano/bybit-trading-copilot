# Roadmap

## Milestone 0 — Foundation
- [x] FastAPI service
- [x] Bybit public REST client
- [x] 12/21 EMA
- [x] Stoch RSI 14/14/3/3
- [x] Fib engine
- [x] Position sizing
- [x] Correlated portfolio-risk calculation
- [x] 1H/4H market snapshot

## Milestone 1 — Live Bybit intelligence
- [x] WebSocket collector for public trades
- [x] WebSocket L2 order-book reconstruction
- [x] Depth bands: 10/25/50 bps
- [x] Aggressive buy/sell delta
- [x] Book add/remove/replenishment metrics
- [x] Open-interest ingestion
- [x] Funding history + live funding state
- [x] Build in-memory 1s/1m derived feature bars
- [x] Price-response features for flow-vs-price divergence
- [x] Initial absorption / continuation classifier
- [ ] Persist 1s/1m derived feature bars
- [ ] Calibrate reaction thresholds by symbol/liquidity regime
- [ ] Failed-auction detector using multi-bar state

> Note: raw L2 size reductions are stored as `removed`, not blindly called `cancelled`, because an order-book delta alone cannot prove whether size was cancelled or executed.

## Milestone 2 — Playbook engine
- [x] Manual Fib anchors in evaluation API
- [x] Manual range definitions in evaluation API
- [x] Trend Pullback / Range Long / Range Short
- [x] Setup state machine
- [x] Evidence-first conditions with no numeric score
- [ ] Persist watched setups and manual Fib/range definitions
- [ ] Detect trigger/reclaim events from lower-timeframe price action
- [ ] Alerts only when setup state changes

## Milestone 3 — Execution coach
- [ ] Trade lifecycle
- [ ] Probe/add/reduce plan
- [ ] Hard invalidation + thesis-warning levels
- [ ] R-multiple and TP ladder calculations
- [ ] Block adds that exceed risk budget
- [ ] Correlated exposure warnings

## Milestone 4 — Read-only account integration
- [ ] Bybit private API read-only credentials
- [ ] Import positions, fills, stops, TP orders
- [ ] Never submit/cancel orders
- [ ] Automatic journal reconciliation

## Milestone 5 — Cross-venue context
- [ ] Binance
- [ ] Hyperliquid
- [ ] Venue divergence
- [ ] Dynamic price-discovery weighting

## Research lab (separate from production playbook)
- [ ] Calendar seasonality
- [ ] Rosh Hashanah → Yom Kippur window
- [ ] Lunar eclipse / moon hypotheses
- [ ] New indicator experiments
- [ ] Promote only after robust backtests
