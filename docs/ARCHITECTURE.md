# Architecture

## Core loop

```text
Bybit market data
      ↓
Normalized market state
      ↓
Regime (12/21 EMA)
      ↓
Location (Fib / range)
      ↓
Reaction (order book / flow / funding / later OI)
      ↓
Setup state machine
      ↓
Risk + execution coach
      ↓
Trader executes manually on Bybit
      ↓
Trade lifecycle + review
```

## V1 data sources

Bybit V5 public market data only:

- Klines: 1H / 4H / 1D / 1W
- L2 order book
- Public trades (next milestone)
- Funding
- Open interest (next milestone)

Private API integration, when added, should default to **read-only** for positions/orders/history. The system must not submit or cancel orders.

## Storage

Start simple:

- Postgres / Supabase: app state, playbooks, plans, trade lifecycle, derived features
- Object storage / Parquet: raw historical research extracts

Do not add ClickHouse until sustained L2 event volume makes Postgres materially painful.

## Tables

### `market_candle`
- venue
- symbol
- timeframe
- ts
- open/high/low/close/volume

### `market_feature`
- symbol
- timeframe
- ts
- ema12/ema21
- ema slopes/spread
- stoch_rsi_k/d
- regime

### `range_definition`
- id
- symbol/timeframe
- high/low/midpoint
- start/confirmed timestamps
- status

### `fib_definition`
- id
- symbol/timeframe
- anchor_low/high
- direction
- anchor_method (manual first)

### `liquidity_feature`
- symbol
- ts
- bid/ask depth at bps bands
- book imbalance
- add/cancel rates
- aggressive buy/sell flow
- absorption/replenishment (phase 2)

### `setup_instance`
- playbook
- state
- context snapshot
- location snapshot
- confirmation snapshot
- trigger state

### `trade_lifecycle`
- symbol / side
- thesis
- max risk
- hard invalidation
- thesis warning level
- status

### `execution_decision`
- lifecycle_id
- ts
- decision: probe/add/hold/reduce/exit
- planned quantity
- actual quantity
- reason

### `trade_review`
- realized_r
- mfe/mae
- entry quality
- chased / early / late
- stop discipline
- playbook followed
