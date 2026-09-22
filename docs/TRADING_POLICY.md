# Trading policy

The copilot exists to improve decision quality and execution discipline. It is not an automated strategy.

## Core philosophy

`regime -> location -> reaction -> execution -> review`

- **Regime**: 12/21 EMA structure and range/trend context.
- **Location**: Fib levels, range extremes, prior highs/lows and manually defined HTF zones.
- **Reaction**: Stoch RSI reset, price response, order flow and liquidity behavior.
- **Execution**: trader decides whether to probe, add, hold, reduce or exit.
- **Review**: distinguish setup quality from execution quality.

## Risk policy

1. Structural invalidation is chosen before position size.
2. Position size is derived from the invalidation and a fixed account-risk budget.
3. Correlated positions share one portfolio risk budget.
4. Adds are only allowed when they were defined in the trade lifecycle or a new confirmation event materially improves the thesis.
5. Never widen a stop solely to avoid realizing a loss.
6. A profitable rule-breaking trade is still graded as poor execution.

## Scale-in policy

Dynamic execution must not become averaging down without confirmation.

A scale-in plan records:
- maximum total risk in R,
- initial probe size,
- add conditions,
- reduce conditions,
- hard invalidation,
- thesis-warning levels.

If an add condition is not met, the copilot should return `ADD_NOT_ALLOWED` even when the price is more favorable.

## Research isolation

Calendar effects, lunar/eclipse dates, seasonality and new indicators remain in the research layer until tested. They may be displayed as context, but they cannot independently move a setup into `READY`.
