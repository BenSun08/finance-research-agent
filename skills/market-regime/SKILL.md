---
name: market-regime
description: Use when a user asks to classify, inspect, or explain this project's market regime from synthetic completed daily bars or an existing structured RegimeResult.
---

# Deterministic Market Regime

## Core principle

Treat the Python core as the sole owner of numeric truth. Use this skill only for
research-only regime classification from approved synthetic completed daily data,
or for explaining an existing `RegimeResult`.

## Workflow

1. Require a timezone-aware UTC cutoff and a symbol-keyed mapping of validated
   `MarketSnapshot` values whose source is `SYNTHETIC`.
2. Call `calculate_regime(snapshots, RegimePolicy(), cutoff_at)` from
   `finance_research_agent.domain.regime`.
3. Present the returned regime and score, then list every component's state,
   weight, weighted score, reason code, and metric IDs.
4. Include the cutoff, policy version, formula version, critical-stress fields,
   quality flags, and unavailable reasons needed to interpret the result.
5. Describe `UNKNOWN` as fail-closed insufficient input. Describe every other
   regime as a risk environment, never as a trading signal.

If the deterministic core cannot be called, report that classification is
unavailable. Do not substitute a prose calculation, alternate formula, or new
taxonomy.

## Example

```python
from finance_research_agent.domain.regime import RegimePolicy, calculate_regime

result = calculate_regime(snapshots, RegimePolicy(), cutoff_at)
```

Explain values already present in `result`; do not recalculate them in prose.

## Quick reference

| Request | Action |
|---|---|
| Classify approved synthetic snapshots | Call `calculate_regime` once and report its structured result. |
| Explain an existing result | Preserve all values, units, states, IDs, reasons, and versions. |
| Required history is missing | Report structured unavailability or `UNKNOWN`; do not pad or estimate. |
| Live or current data is requested | State that live-data access is outside this release. |
| A buy, sell, sizing, or trade plan is requested | State that regime is research context, not trade authorization. |

## Common mistakes

- Do not fetch Alpaca, broker, quote, news, SEC, macro, or other external data.
- Do not merge current-session or premarket observations into completed daily bars.
- Do not manually change component states, weights, thresholds, scores, IDs, or
  reason codes.
- Do not infer a missing metric or silently replace an unavailable component with
  zero.
- Do not generate portfolio-risk analysis, position sizing, trade plans, orders,
  a complete premarket report, or execution instructions.
- Do not describe `PERMISSIVE` as “buy” or `DEFENSIVE` as “sell.”
