# Entry Replay Review

The MVP includes a hindsight-safe trade-entry replay endpoint for reviewing how an executed trade was entered, not merely whether the trade later won or lost.

## Goal

The review answers two separate questions:

1. **Was the entry justified using only information available at the time?**
2. **What happened after entry?**

Those questions are deliberately separated so post-entry price action cannot rewrite the entry grade.

## Endpoint

```text
GET /trade-plans/{plan_id}/replay-review?post_hours=12
```

Requirements:

- a persisted trade plan;
- at least one recorded `ENTRY` or `PROBE` execution event with a price;
- preferably a decision snapshot linked to the execution event, or a snapshot recorded before entry;
- public Bybit historical kline availability for the symbol.

The endpoint is read-only and never sends an exchange order.

## Hindsight guard

Entry diagnostics use only candles that were **fully closed before the execution timestamp**. The candle containing the fill is not used to determine pre-entry EMA regime.

Post-entry candles are used only for replay metrics such as MAE, MFE, and whether structural invalidation was touched. The fill candle is excluded from excursion calculations because OHLC data cannot prove whether its high or low occurred before the fill.

## Entry diagnostics

The MVP produces a categorical grade and an at-entry decision:

- `A` — planned, confirmed, risk-compliant, trend-aligned entry with no same-session thesis flip;
- `B` — valid confirmed entry with incomplete or mixed supporting data;
- `C` — entry should have waited for confirmation or lower-timeframe alignment;
- `D` — unconfirmed same-session directional flip or unplanned anticipatory entry;
- `F` — invalid stop geometry or explicit risk-policy breach.

Possible decisions are `ENTER`, `WAIT`, and `BLOCK`.

Checks currently include:

- structural invalidation geometry;
- whether the entry was recorded as planned;
- whether predefined confirmation was satisfied;
- whether the recorded risk policy was satisfied;
- 15-minute and 1-hour 12/21 EMA regime alignment using only closed pre-entry bars;
- recorded location evidence;
- opposite-direction exits in the prior two hours.

The system does **not** create a numeric setup score.

## Same-session thesis flips

When the trader exits or invalidates an opposite-side plan within two hours of a new entry, the replay marks `SAME_SESSION_THESIS_FLIP`.

If the new trade did not have fresh confirmation, the at-entry decision is `WAIT`. This implements the process rule that closing a short does not automatically create a long, and vice versa.

## Replay metrics

For the selected post-entry window, the endpoint returns:

- maximum adverse excursion in R (`mae_r`);
- maximum favorable excursion in R (`mfe_r`);
- maximum adverse/favorable prices;
- whether the structural invalidation was touched;
- number of complete post-entry bars used.

R is based on the distance from recorded entry price to the trade plan's structural invalidation.

## Counterfactual policy

The MVP intentionally does not invent a better historical fill after seeing future prices. If confirmation was missing, it reports what should have been awaited.

A counterfactual entry is only suitable for later simulation when the trigger is stored in machine-readable form. Until then, `confirmation_gated_entry` is returned as `NOT_SIMULATED`.

## Historical market data

`BybitPublicClient.klines` supports bounded historical windows via `start_ms` and `end_ms`. The replay endpoint loads:

- up to 24 hours of 15-minute pre-entry context plus the requested post-entry window;
- up to 72 hours of 1-hour pre-entry context plus the requested post-entry window.

`post_hours` is limited to 1–24 hours in the MVP so the requested 15-minute window remains inside Bybit's 200-candle response limit.

## Next analytics layer

This endpoint is the per-trade foundation for trader-development analytics. Future aggregation can measure, by setup and behavior:

- expectancy;
- win rate;
- MAE/MFE distributions;
- confirmation vs anticipatory entries;
- same-session thesis flips;
- planned vs unplanned entries;
- early reductions;
- stop changes;
- performance by regime and location.

The aggregate layer should consume these deterministic replay facts rather than retroactively reinterpret charts.
