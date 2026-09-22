# Trading Playbooks

The production playbook intentionally starts with only three setups. The goal is to make execution more consistent, not to accumulate more indicators.

## Decision hierarchy

```text
REGIME → LOCATION → MOMENTUM / REACTION → TRIGGER → MANUAL EXECUTION
```

A good level is not automatically a good entry.

## Setup states

```text
NO_SETUP
  ↓
CONTEXT_VALID
  ↓
APPROACHING_LOCATION
  ↓
AT_LOCATION
  ↓
CONFIRMATION_DEVELOPING
  ↓
WAITING_FOR_TRIGGER
  ↓
TRIGGERED
  ↓
READY
```

A setup can move to `INVALIDATED` at any point when its predefined invalidation is breached.

`READY` means the documented playbook conditions are present. It is decision support, not an order instruction.

## 1. Trend Pullback

### Context
- Long: 4H regime is an uptrend.
- Short: 4H regime is a downtrend.
- Matching 1H regime is preferred and is required for the shallow Fib zone.

### Location
The engine identifies four explicit retracement zones from manually selected swing anchors:

- shallow: `0.382–0.500` — only actionable when 1H and 4H trend align
- mid: `0.500–0.618`
- primary: `0.618–0.786`
- deep: `0.786–0.886`

The Fib anchors remain manual in V1 because selecting the meaningful impulse is a discretionary market-structure decision.

### Momentum
For longs, supportive Stoch RSI behavior includes oversold conditions, exiting oversold, or a bullish K/D cross while still in the lower half of the oscillator. Shorts use the inverse logic.

### Reaction
At a long location, `sell_absorption` or subsequent `buy_continuation` can support the setup. At a short location, `buy_absorption` or `sell_continuation` can support it.

Reaction is confirmation only. A book imbalance or liquidity wall is never an entry by itself.

### Trigger
The trigger is explicit and auditable. In V1 the trader supplies whether the chosen reclaim / failed-auction / lower-timeframe structure trigger has occurred.

## 2. Range Long

- 4H regime = `RANGE`
- price in the bottom 20% of the defined range
- Stoch RSI reset preferred
- `sell_absorption` is supportive reaction evidence
- explicit reclaim / return-inside-range trigger

The bottom 30% is considered `APPROACHING_LOCATION`; the middle of the range is context only.

## 3. Range Short

Inverse of Range Long:

- 4H regime = `RANGE`
- price in the top 20% of the range
- overbought / momentum rollover preferred
- `buy_absorption` is supportive reaction evidence
- explicit bearish trigger

## Execution lifecycle

Playbook readiness and position execution are separate concepts.

```text
WAIT → PROBE → ADD → HOLD → REDUCE → EXIT / INVALIDATE
```

Adds are only allowed when a predefined confirmation condition occurs. "Price is worse so average down" is never a valid add condition.

## API

`POST /playbook/evaluate` returns:

- setup state, never a 0–100 score
- passed and missing conditions
- Fib/range location evidence
- Stoch RSI evidence
- current reaction state when supplied or available from the live Bybit stream
- `ready_for_manual_execution`

The endpoint never submits an order.
