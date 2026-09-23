# Regime and Setup Methodology

This note records the deterministic R6 setup and candidate-ranking rules. It is
not a trading recommendation or authorization. R6 detects and ranks research
candidates only; it does not create trade plans, size positions, or execute
orders. Existing regime classification is an input to ranking and is not
changed by R6.

## Evidence and eligibility

R5 instrument eligibility, event, capability, and data-quality decisions are
hard pre-score gates. A blocked gate or missing required capability produces an
exclusion, not a low score. Setup assessment admits only USD snapshots whose
source, retrieval, observation, and evidence-cutoff times meet the live run's
cutoff. Late live evidence requires a new immutable revision. Historical replay
retains its separate retrieval semantics.

## Setup families

R6 recognizes only `BREAKOUT_CONTINUATION` and `TREND_PULLBACK`; their windows,
buffers, tolerances, extension limits, liquidity minimums, and reward/risk
requirements come from the validated, frozen `SetupPolicy`.

- A breakout requires positive primary and intermediate trends, rising
  intermediate trend, positive benchmark relative strength, non-negative sector
  relative strength, price within the policy ATR proximity of the completed-base
  resistance, and policy-compliant extension and scenario levels. Its entry
  condition additionally requires a clear of the entry zone with consolidated
  volume confirmation above the completed-base median.
- A pullback requires the existing positive trend and structure to remain
  intact, price to be within the policy support tolerance, a controlled
  retracement within the policy limit, and a deterministic re-strengthening
  condition. A decline without the prior trend and re-strengthening evidence is
  not a pullback.

Invalid evidence, unavailable required metrics, insufficient liquidity, a
failed setup condition, or a setup that misses a policy threshold is retained
as an exclusion with reason codes and gate evidence. Invalid policy
configuration fails closed. Zero eligible setups is valid.

## Candidate scoring

Each eligible candidate receives six normalized component qualities in `[0, 1]`.
Points are quality multiplied by the frozen policy weight:

| Component | Weight | Deterministic quality basis |
|---|---:|---|
| `SETUP_QUALITY` | 25 | One minus extension |
| `TREND_QUALITY` | 20 | Intermediate trend metric scaled by 25 and clamped |
| `RELATIVE_STRENGTH` | 20 | Benchmark relative strength scaled by 25 and clamped |
| `REWARD_RISK_QUALITY` | 15 | Best policy target reward/risk divided by that multiple plus one |
| `LIQUIDITY_QUALITY` | 10 | Median dollar volume divided by five times the policy minimum, clamped |
| `CATALYST_EVIDENCE_QUALITY` | 10 | Zero unless verified catalyst evidence is supplied; R6 does not invent it |

The exact weight schedule is validated at both setup and scoring boundaries.
Positive score is the sum of component points. The following separate visible
penalties are subtracted, with a zero floor: extension, event uncertainty,
correlation concentration, and data quality. An event warning costs 5 points;
correlation grouping applies a 10-point penalty to each lower-ranked connected
alternative at absolute correlation of at least `0.90`. Required missing or
failed data is blocked before scoring and can never be offset by component
points.

Data quality is also an explicit tie-break derived from frozen R5 outcomes:
global `PASS`/symbol `DRAFT` rank as `1`, `DEGRADED`/`REVIEW_REQUIRED` as `0.5`,
and `FAIL`/`BLOCKED` as `0`; the global and symbol ranks are multiplied. Failed
and blocked states remain hard gates, so a rank never makes an ineligible
candidate scoreable.

## Ranking and selection

Candidates sort by descending total score, setup quality, relative strength,
reward/risk quality, data-quality rank, and liquidity rank, then ascending
symbol and candidate ID. The ranking operation uses the fixed R6 Decimal
context. In permissive regimes the minimum selection score is 70; in neutral
regimes it is 80; defensive and unknown regimes select no new candidates. At
most five candidates are selected for plans and at most three are highlighted.
Every scored candidate remains in deterministic output so near misses stay
visible. Highly correlated connected candidates retain one pre-penalty primary;
the rest remain marked as secondary alternatives with their correlation
evidence and visible penalty.
