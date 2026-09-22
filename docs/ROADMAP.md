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
- [ ] Persist 1s/1m derived liquidity features
- [ ] Absorption / failed-auction detector
- [ ] Short-term price-response features for flow-vs-price divergence

> Note: raw L2 size reductions are stored as `removed`, not blindly called `cancelled`, because an order-book delta alone cannot prove whether size was cancelled or executed.

## Milestone 2 — Playbook engine
- [ ] Manual Fib anchor UI/API
- [ ] Range definitions
- [ ] Trend Pullback / Range Long / Range Short
- [ ] Setup state machine
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
