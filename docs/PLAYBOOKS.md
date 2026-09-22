# Trading Playbooks

The live system starts with only three playbooks.

## 1. Trend Pullback

**Context**
- 12 EMA above 21 EMA for long (inverse for short)
- Both slopes aligned

**Location**
- Primary pullback zone: 0.618–0.786
- Shallow pullback: 0.382–0.500 if trend is strong

**Momentum**
- Stoch RSI resets in the direction opposite the trend, then turns back

**Reaction**
- Watch order-book behavior and executed flow at the level
- A wall alone is not confirmation

**Trigger**
- Reclaim / failed auction / lower-timeframe structure confirmation

## 2. Range Long

- Regime = RANGE
- Price near lower range extreme
- Fib confluence preferred
- Stoch RSI reset/oversold
- Look for sell absorption or failed downside auction
- Trigger on reclaim / return inside range

## 3. Range Short

Inverse of Range Long.

## Execution lifecycle

A setup is not a binary entry.

```text
WAIT → PROBE → ADD → HOLD → REDUCE → EXIT / INVALIDATE
```

Adds are only allowed when a **predefined confirmation condition** occurs. "Price is worse so average down" is never a valid add condition.
