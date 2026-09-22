# Next engineering steps

## PR #2 — Playbook engine

- Add explicit playbook definitions for:
  - trend pullback long/short
  - range long/short
- Add a setup evaluator that consumes normalized market state.
- Keep outputs state-based rather than score-based.
- Add manual Fib anchors and range definitions as first-class entities.
- Add a portfolio-risk gate before a setup can become `READY`.

## PR #3 — Trade lifecycle journal

- Trade thesis
- Planned probe/add/reduce steps
- Actual executions
- Structural and thesis-warning invalidations
- R-multiple tracking
- Execution-quality flags: early, chased, oversized, stop moved, plan followed

## PR #4 — Persistence and UI

- Postgres/Supabase application state
- Historical market-feature persistence
- Trader dashboard
- Setup inbox
- Live market detail page
- Post-trade review page

## Data expansion

Bybit is the execution venue and first live feed. Cross-venue price discovery/liquidity should be added later (Binance/Hyperliquid) without changing the canonical market-state contract.
