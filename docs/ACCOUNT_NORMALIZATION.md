# Account normalization

The read-only Bybit client returns exchange-shaped JSON. The normalization layer converts that snapshot into the trading copilot's domain model so risk, execution review, and future journaling do not depend on Bybit response shapes.

## Flow

```text
Bybit positions + executions + open orders + wallet
                    |
                    v
          normalize_account_snapshot
                    |
                    v
NormalizedAccount / NormalizedPosition / NormalizedOrder / NormalizedFill
                    |
                    v
              /portfolio/live
```

## Structural risk

For each open position, the normalizer looks for a valid protective stop in this order:

1. `stopLoss` attached to the Bybit position;
2. an opposite-side, reduce-only open order whose trigger is beyond the average entry in the invalidation direction.

For a long, a valid stop is below average entry. For a short, a valid stop is above average entry.

Structural risk is:

```text
quantity * abs(stop - average_entry)
```

If no valid stop is found, the position is marked `protected = false`, its structural risk is left unknown, and the portfolio is treated as outside policy even if known risk is below the configured budget.

## Correlation policy

V1 assigns open crypto positions to the `crypto_beta` correlation group. This intentionally treats simultaneous BTC/ETH/BNB directional exposure as one portfolio thesis for risk budgeting. Asset-specific and dynamically estimated correlation groups can be added later.

## Endpoints

- `GET /account/snapshot` — raw read-only Bybit response.
- `GET /account/normalized` — normalized domain state.
- `GET /portfolio/live` — portfolio-level risk view used by the execution coach and future dashboard.

No endpoint in this layer can place, amend, or cancel an exchange order.
