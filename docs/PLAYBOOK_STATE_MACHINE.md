# Playbook state machine

The system is a decision-support tool. It never places trades.

## Core states

1. `NO_SETUP` — no relevant regime/location alignment.
2. `CONTEXT_VALID` — regime supports one of the defined playbooks.
3. `APPROACHING_LOCATION` — price is nearing a meaningful Fib/range/HTF level.
4. `AT_LOCATION` — price is inside the execution zone.
5. `CONFIRMATION_DEVELOPING` — momentum/liquidity/flow evidence is forming.
6. `WAITING_FOR_TRIGGER` — confirmation exists but the explicit price trigger has not occurred.
7. `TRIGGERED` — price-action trigger occurred.
8. `READY` — trigger plus risk checks pass; trader may choose to execute manually.
9. `INVALIDATED` — context or structural invalidation failed.

## Evidence buckets

### Regime
- 12/21 EMA ordering
- EMA slope/separation
- trend / range / transition classification

### Location
- Fib proximity
- range position
- prior swing high/low proximity
- optional manually-defined HTF levels

### Momentum
- Stoch RSI state/reset

### Microstructure
- 10/25/50 bps depth imbalance
- aggressive buy/sell trade flow
- spread
- bid/ask additions and removals
- replenishment
- funding and open interest context

### Trigger
The trigger is explicit and playbook-specific. Examples:
- deviation and reclaim
- failed auction through a range extreme
- lower-timeframe structure break after a Fib reaction

## Risk gate

`READY` is not allowed when the proposed execution breaches the portfolio risk budget. Correlated crypto-beta positions are evaluated together rather than as independent trades.

## No false precision

The UI should prefer evidence and states over a single synthetic score. Example:

```text
BNBUSDT RANGE SHORT

Regime       RANGE                    OK
Location     upper 8% of range        OK
Stoch RSI    exiting overbought       OK
Liquidity    sell response developing WATCH
Trigger      deviation reclaim        WAIT
Risk         1.7% aggregate           OK

STATE: WAITING_FOR_TRIGGER
```
