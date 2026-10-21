# Trading Agent Specification

Status: product design approved on 2026-10-14; implementation pending the gates
in [tasks](tasks.md). This is the sole current product specification. Governance
is in the [constitution](constitution.md); precise existing implementation
contracts are retained in [contracts](contracts.md).

## Product outcome

Build a local-first agent that researches a known candidate pool, makes
explainable portfolio decisions, executes them in a local simulator, tracks
positions and cash, and evaluates decisions against subsequent outcomes. The
agent has an accountable decision loop. Research briefs and skills become
interfaces and outputs of that loop.

The first runtime is ChatGPT Work, started manually by the user. A local service
provides deterministic evidence, risk validation, simulated execution, and durable
records. A local dashboard exposes configuration and results. Actual Work access
to local operations must be proven before production integration; a plugin that
works in Codex is not evidence that it works in the user's Work environment.

## Clarifications

### Session 2026-10-19

- Q: If the app is not run for several trading days, how should previously accepted orders and protective stops be processed? → A: Catch up on restart, reconciling missed sessions chronologically under the previously accepted orders and frozen policies before new decisions; distinguish simulated event time from processing time and block affected processing when required market data is unavailable.
- Q: When Pause is active, should the agent still be allowed to research and reduce existing positions? → A: Pause new exposure: cancel pending buys that have not yet become effective and reject new buys or increases; allow research, reductions, exits, protective stops and reconciliation. Do not undo earlier simulated events or automatically restore cancelled orders on resume.
- Q: If a price gap makes an accepted buy exceed available cash or a risk limit at execution, should the simulator reject the order or buy fewer shares? → A: Reject the entire order after rechecking cash and risk using the simulated execution price, fees and slippage; execute nothing, record the reason, and require a new validated proposal for any later buy.
- Q: Should ordinary simulated buy and sell orders use the next eligible session's opening or closing price? → A: Use the next eligible regular session's opening price with configured slippage and fees; previously accepted protective stops retain their separate execution rules.
- Q: When should proceeds from a simulated sale become available to fund another purchase? → A: Model T+1 settlement: track settled cash and unsettled sale proceeds separately, make sale proceeds usable on the next settlement business day with weekends and settlement holidays respected, and prohibit purchases funded by unsettled proceeds.

### Session 2026-10-20

- Q: Should the first release simulate protective stops from daily opening and lowest prices or require intraday data? → A: Use the daily-data model: an existing long position gaps through its stop at the opening price, otherwise a daily low touching the stop fills at the stop price, with configured slippage and fees. Protect a new opening-price buy immediately, reject it if its execution price already violates its stop, and do not invent an exact intraday trigger time.
- Q: Should ordinary orders be valid only for their intended session's opening or remain eligible for later sessions? → A: Use one intended opening and fill the full accepted quantity or nothing. Confirmed inability to execute at that opening expires the order; there are no partial fills or automatic carryover. Missing historical data leaves the outcome unresolved for catch-up against the original session, and a later trade requires a new proposal. Protective stops retain their separate lifecycle.

### Session 2026-10-21

- Q: Should the first release automatically account for ordinary splits and cash dividends or stop processing affected positions? → A: Support common events using verified data and unadjusted execution/valuation prices. Apply split adjustments to holdings, per-share cost basis and outstanding order/stop quantities and prices; record dividend entitlements as receivables and cash on the payment date without automatic reinvestment. Unsupported or incomplete events, including fractional remainders without verified cash-in-lieu terms, remain unresolved without invented proceeds or discarded holdings.
- Q: Which benchmark portfolios should the first release use to compare the agent's results? → A: Use a zero-interest cash portfolio and an equal-weight buy-and-hold portfolio from the predeclared candidate pool, buying whole shares at the first eligible opening and retaining leftover cash without scheduled rebalancing. Match initial capital, dates, data, costs, settlement and corporate-action assumptions; preserve benchmark history when configuration changes. A separate deterministic trading-strategy benchmark is deferred.
- Q: When required portfolio valuations are missing, should the performance report show gaps or estimate the missing values using the last known prices? → A: Keep every scheduled evaluation date and report coverage and missing-input reasons. Mark a metric unavailable when its required inputs are incomplete; independent metrics with complete inputs may still be reported. Do not carry forward prices to estimate performance or silently drop missing dates. Recovered data creates a new evaluation revision that preserves the earlier record.

### Session 2026-10-22

- Q: At a session's opening, if an existing position has both a triggered protective stop and a pending ordinary sell order, which should execute first? → A: Execute the protective stop for the entire existing position, then cancel the overlapping ordinary sell as superseded. Record one exit fill with its configured costs. If the opening does not trigger the stop, ordinary opening sells occur first and a later daily-low stop applies only to remaining shares; never sell the same shares twice.
- Q: When replacing an accepted order, should a failed replacement leave the original order active or cancel it? → A: Keep the original active until a validated replacement takes over; apply the old cancellation and new instruction together, and retain the original if validation fails. Ordinary cancellations/replacements must be accepted before the intended opening, even if reconciliation is delayed. Protective-stop replacements take effect no earlier than the next eligible session while existing protection continues. Explicit cancel-only requests remain separate, recorded actions subject to timing and safety checks.
- Q: When part of a position is sold, should the simulator calculate its profit using the average purchase cost or the cost of the oldest shares? → A: Use weighted-average cost per position. New purchases combine their acquisition cost with the remaining position cost; sales remove a proportional share of that cost. Include purchase fees in acquisition cost, deduct sale fees from proceeds, and account for slippage through modeled fill prices without a second deduction. Keep ordinary cash dividends as separate income and preserve original fill history for reproducibility.
- Q: Should the official performance and drawdown reports use portfolio values at every trading day's close or only at each week's final close? → A: Use every regular trading session's close, including days without an agent cycle, and reconstruct those valuations during catch-up. After reconciling effective events, equity equals settled cash plus unsettled sale proceeds plus outstanding dividend receivables plus remaining shares valued at unadjusted closing prices. Unrealized trading profit/loss is that share value minus remaining acquisition cost. Match agent/benchmark observation dates, preserve missing-input handling, and label drawdown as daily closing drawdown rather than an intraday measure.
- Q: Should each first-release evaluation use a fixed initial capital amount, with no later deposits or withdrawals? → A: Yes. Start with one positive cash funding amount before the first eligible opening; allow no later external deposits or withdrawals in that evaluation. Changing starting capital creates a new independent simulated portfolio/evaluation while preserving earlier history. Use ending equity divided by initial capital minus one for cumulative return, successive scheduled closing equities for daily returns, and the largest decline from the running peak of initial capital and closing equities for daily closing drawdown. Match benchmark definitions, expose unavailable inputs/nonpositive daily-return denominators, and do not count costs or dividends twice.

## Current implementation and target

The checked-in `0.5.0.dev0` baseline provides deterministic research, frozen
packets, validated publication, replay, evaluation, a local stdio MCP boundary,
three skills, and a separate bounded generic AgentRuntime. Operational composition
uses Alpaca market data and a local exchange calendar. Missing event/macro source
capabilities remain explicit and currently block actionable research plans.

The six-role research team, production Work integration, dashboard, simulated
orders/fills/positions/ledger, and portfolio decision loop are target capabilities.
Their approval here does not make the corresponding code available. Existing
research states and outputs retain their meanings during migration.

## User scenarios

1. The user configures a candidate pool, simulated capital, risk limits, and
   execution assumptions. After the market closes, the user starts one cycle
   in Work. Analysts examine evidence, a challenger tests the theses, and the
   decision-maker proposes portfolio actions. The local service validates and
   stores accepted simulated orders for the earliest eligible next session.
2. On subsequent cycles, the service reconciles simulated orders and positions
   against eligible market data, including missed sessions in chronological order,
   before presenting new hold, reduce, exit, buy, and no-trade decisions. It records
   costs and portfolio value without inventing decisions for unattended sessions.
3. A candidate has missing required company evidence. Its new exposure is
   blocked with a reason; unaffected candidates can proceed. Existing exposure
   stays visible and protection continues only with valid inputs.
4. Work stops mid-cycle. A new conversation loads durable run state and resumes
   the permitted stage without silently refreshing frozen evidence or applying
   an order twice.
5. The user inspects decisions, counter-evidence, risk rejections, simulated
   fills, portfolio results, and reflections, then explicitly approves any
   proposed policy change for a later run.

## Requirements and acceptance

### FR-01 Instruments and portfolio

Support U.S.-listed common stocks and non-leveraged, non-inverse ETFs. The first
release uses one local simulated USD cash account, long-only integer shares,
configurable initial capital, and no leverage. Research initially operates on a
user-maintained known candidate pool; full-market discovery is deferred. A pool
of roughly 10–30 is a planning expectation, not an unvalidated universal hard cap.

Each evaluation starts with one positive initial cash funding amount. External
deposits and withdrawals during that evaluation are excluded from the first
release. Changing starting capital creates a new independent simulated
portfolio/evaluation and preserves the previous history. Dividends and settlement
transfers remain internal portfolio events, not external funding.

Track settled cash separately from unsettled sale proceeds. Model T+1 settlement:
sale proceeds become available to fund purchases on the next settlement business
day, respecting weekends and settlement holidays. Only settled cash may fund a
buy fill; same-day sale proceeds cannot be reused before settlement. This is an
explicit simulator policy, not a claim to reproduce every brokerage cash rule.

Acceptance: reject unsupported instruments, shorts, fractional-share proposals,
and leverage; preserve policy identity and the initial funding event; demonstrate
cash and position conservation through a complete synthetic lifecycle. Verify
that unsettled proceeds cannot fund a buy, weekend/holiday settlement is applied
consistently during daily processing and catch-up, and settlement does not count
sale proceeds twice. Reject nonpositive initial capital and later external funding
changes within an evaluation; a new starting-capital choice must not rewrite the
prior portfolio or its evaluation. Define the calendar and accounting transitions in
[RQ-03](research.md#rq-03-simulation-semantics) before implementing accounting.

### FR-02 Cadence and bounded cycles

Run daily after the regular close, with decisions eligible for execution no
earlier than the next trading session. The intended holding horizon is days to
weeks. The user manually starts cycles in Work; unattended scheduling and direct
dashboard-to-Work launch are not first-release prerequisites. Every cycle has
explicit identity, stages, cutoff, deadline, tool/research limits, and terminal
outcomes. Exact limits must be versioned and resolved in the runtime spike.

After downtime, reconcile missed completed sessions chronologically using orders
and protective stops accepted before their eligible execution, with their frozen
policies and valid session data, before making new decisions. Downtime alone does
not cancel an order or disable its protection; the original eligibility and
expiry rules still apply. Catch-up requires no continuously running service and
must not create retrospective agent decisions. Missing required data blocks the
affected processing and dependent decisions under FR-07.

Acceptance: an ordinary order cannot fill in its decision session or before its
decision; protection for a new entry follows FR-06. Calendar holidays, late
starts, interrupted cycles, duplicate requests, and exhaustion have explicit
tested outcomes. Given identical accepted orders, policies and session data,
daily reconciliation and catch-up produce identical simulated effects; recorded
processing times may differ. A bounded no-trade result completes a cycle.

### FR-03 Six research and decision roles

| Role | Responsibility and required inspectable output |
| --- | --- |
| Shared market analyst | Common market, regime, macro/event context and limitations reused across candidates |
| Technical analyst | Price/volume structure, deterministic metrics, scenario evidence, uncertainty |
| Fundamentals analyst | Company financial/business evidence or ETF-appropriate structure/exposure analysis |
| News and events analyst | Relevant developments, source authority, timing, verified and unverified event risks |
| Challenger | Strongest counter-thesis, conflicts, missing evidence, invalidation and targeted research requests |
| Portfolio decision-maker | Portfolio-aware proposed actions, prioritized rationale, alternatives, risk requests and abstentions |

Distinct roles require inspectable artifacts and attribution, not six model
vendors or simultaneous workers. Work may delegate bounded subagents. After the
challenge, allow at most one supplementary research round; unresolved material
gaps produce abstention or the relevant block. The decision-maker consumes the
recorded analyses and challenge, never fabricated role outputs.

Acceptance: an end-to-end trace identifies evidence, each role's contribution,
the challenge and its response, the final proposal, and local validation. Shared
context is reused consistently, and exhaustion cannot create an unbounded debate.

### FR-04 Evidence and judgment

Collect technical, fundamentals, news/company-event, and shared market/macro
evidence through approved sources. Required evidence varies by instrument and
action under explicit policy. Preserve timestamps, provenance, quality,
conflicts, limitations, and immutable cutoff-bound inputs. Record subjective
judgment separately from deterministic facts and calculations.

An agent may propose an investment outside the existing breakout/pullback setup
families. Those setup tools provide evidence and scores; they are not universal
vetoes on the new agent. Instrument, evidence, cash, and risk gates remain hard
constraints. The new required-source matrix and source availability are a
prerequisite, not an assumption that old source limitations disappeared.

Acceptance: post-cutoff evidence needs a new revision; unsupported numerical or
factual claims cannot be promoted into authoritative fields; missing required
evidence blocks the affected exposure regardless of narrative confidence.

### FR-05 Decisions and deterministic risk

Support buying, holding, reducing, exiting, and declining a trade. Proposals
include thesis, counter-thesis, evidence, invalidation/stop intent, horizon,
requested allocation, and uncertainty. Deterministic services check the frozen
portfolio and policy, compute permissible quantity and costs, and either construct
an accepted order or record a precise rejection. Do not silently rewrite a
rejected proposal into a different strategy.

Acceptance: cash, per-position and portfolio exposure/risk limits, protective
requirements, stale portfolio state, and conflicting submissions are enforced
independently of Work. Missing numerical policy values are unavailable, not guessed.
User-approved policy changes take effect on a later versioned cycle.

### FR-06 Simulated execution and accounting

Execute ordinary buys and sells at the next eligible regular session's opening
price with configured slippage and fees. A later catch-up run uses that eligible
session's opening price, not a price from the processing day. Protective stops
follow separate versioned execution rules; a gap can fill beyond a stop price,
and a stop is not a guaranteed price. Maintain an auditable
order/fill/cash/position/equity lifecycle with idempotent commands.
Simulated execution is visibly labeled throughout artifacts and the dashboard.
Record simulated event time separately from the actual processing time, including
when fills are recorded during catch-up after downtime.

An ordinary order is eligible only at its intended session's opening. Fill its
full quantity after approved split adjustments when all execution checks pass;
partial fills and automatic carryover to later openings are excluded. If execution at that opening is
confirmed impossible, expire the order with a reason. A later trade requires a
new validated proposal. Cash/risk failures remain terminal rejections, as defined
below. These ordinary-order expiry rules do not expire protective stops.
Approved split adjustments can change the effective quantity while preserving
the original accepted order and adjustment history; they are not partial fills.

Validate a replacement before superseding an accepted instruction. Apply the old
instruction's cancellation and the replacement together at the applicable
effective boundary; neither may be left as a lone successful effect. A failed
validation leaves the original active and records the rejected replacement,
clearly reporting that the original remains in force. Retain both instructions
and their relationship in the audit history.

Accept ordinary cancellation/replacement requests only before the order's
intended opening. At or after that opening, reject changes to that execution
opportunity even when reconciliation is pending. Protective-stop replacements
take effect no earlier than the next eligible session; existing protection
continues until the replacement takes effect. Every change must satisfy the
existing risk and protection requirements. An explicit cancel-only request is a
separate recorded action subject to the same timing and safety checks.

Delayed processing or missing historical market data does not establish that
execution was impossible. Keep the affected outcome unresolved and block
dependent processing until valid evidence resolves the original session. Recovered
data must not move the execution opportunity to the retrieval or processing day.

Use the following daily-data protective-stop model for the first release, with
valid regular-session opening and lowest prices:

- For an existing long position, if the opening price is at or below its stop,
  use the opening price as the simulated exit reference. Otherwise, if the daily
  low is at or below the stop, use the stop price. If neither condition holds,
  the stop does not trigger. Apply configured slippage and fees to either exit.
- When that opening triggers a protective stop and an ordinary reduction or exit
  is also pending for the position, execute the stop for the entire existing
  position first. Cancel the overlapping ordinary sell as superseded, linking
  its reason to the stop execution; record one exit fill and its configured costs.
  If the opening does not trigger the stop, execute eligible ordinary opening
  sells first; a later daily-low stop applies only to the remaining shares.
  An ordinary full exit leaves no shares for a subsequent stop fill. Never sell
  the same shares twice.
- A new opening-price buy is protected immediately after its fill and can stop
  out that same session. Reject the buy if its modeled opening execution price
  is at or below its proposed stop; do not create an already-invalid entry.
- Label these as modeled daily-data outcomes. Record the session and supported
  event ordering without claiming an exact intraday trigger time that the data
  cannot establish. Intraday price data is not a first-release prerequisite.

Before filling an accepted buy, recheck its full quantity after approved split
adjustments against available settled cash and risk limits using the simulated
execution price, configured fees and slippage, applicable frozen policy and
reconciled portfolio state.
If any limit fails, reject the entire order with a precise reason and no fill;
do not automatically reduce its quantity or defer it until conditions improve.
A later buy requires a new validated proposal. Record the rejected order and
validation inputs for audit and deterministic replay.

Use weighted-average acquisition cost for each instrument's position. A purchase
adds its modeled fill value and purchase fees to the remaining position cost;
divide that combined cost by the new share quantity to derive the average. A
sale removes the proportional cost of its shares, with realized trading profit
equal to sale proceeds after sale fees minus that removed cost. The remaining
position retains the residual cost; a full exit consumes all remaining cost.
Slippage is already reflected in the modeled fill price and must not be deducted
again. Record ordinary cash dividends as separate income. Preserve original fills
and cost allocations so the ledger and realized/unrealized results can be replayed.

Account for ordinary splits and cash dividends using verified event terms for
supported instruments. Use unadjusted market prices for execution and valuation;
corporate-action effects are explicit ledger events, never also embedded in
adjusted prices used for those same calculations.

- Apply splits to holdings, per-share cost basis and outstanding order/stop
  quantities and prices together at the effective event boundary. Preserve
  the original records and the adjustment event history.
- Record a cash-dividend entitlement as a receivable when it becomes effective.
  Include the receivable in equity, but not cash available for purchases; move
  it to available cash on the verified payment date without creating a second
  income event. Do not reinvest dividends automatically.
- Unsupported or incomplete events, including mergers, spin-offs, special
  distributions and fractional-share remainders without verified cash-in-lieu
  terms, leave affected processing unresolved. Preserve the holdings and reason;
  do not invent proceeds, discard residual quantities or fabricate a valuation.

At each closing valuation scheduled under FR-09, reconcile all events effective
by that close before valuing remaining shares at unadjusted closing prices.
Portfolio equity is settled cash plus unsettled sale proceeds plus outstanding
dividend receivables plus the market value of remaining shares. Unrealized trading
profit/loss is that share value minus the remaining acquisition cost. Missing
required inputs retain their unavailable status under FR-09.

Acceptance: exact replay reproduces fills and ledger from the same accepted
decisions, data, and simulator version; no duplicate application is possible;
conservation and reconciliation hold. An ordinary fill references the correct
eligible session's opening price and its configured costs. A buy that breaches
cash or risk limits at execution produces a recorded rejection and zero fills,
with no automatic resize or later retry. Verify gap-through, exact stop touch,
no-touch, same-day entry/stop and invalid-entry cases, including fees and slippage,
without fabricating intraday timestamps. Verify an opening stop supersedes a
pending reduction or exit with one full-position fill and no ordinary sell fill;
when only the later daily low triggers the stop, verify opening sells occur first
and only residual shares can stop out, with costs tied to actual fills.
Distinguish confirmed expiry from an unresolved outcome caused by missing
evidence; late data can resolve only the original execution opportunity, without
partial fills or a later-session fill.
Verify split-adjustment conservation, dividend entitlement/payment without double
counting or early spending, and scoped blocks for unsupported corporate actions.
Verify failed replacement retains the original, successful replacement switches
both instructions together, and late cancellation/replacement cannot rewrite an
opening awaiting reconciliation. Verify stop replacement preserves protection
until its effective session and cancel-only requests obey the applicable checks.
Verify weighted-average cost across purchases, reductions, full exits and splits,
with purchase/sale fees counted once and dividend income kept separate. Ignoring
fees, buying 10 shares at USD 100 and 10 at USD 120, then selling 10 at USD 130,
produces USD 200 realized trading profit and USD 1,100 remaining position cost.
Verify closing equity reconciles its cash, receivable and position components,
and unrealized trading results reconcile marked shares with remaining cost.
Before readiness, define arithmetic examples and transitions for the approved
settlement, execution, replacement, cost accounting, corporate-action and
closing-valuation rules under
[RQ-03](research.md#rq-03-simulation-semantics).

Complex orders, limit/trailing-order strategies, and intraday order management
are deferred. No real brokerage endpoints, real orders, or account credentials
are part of this capability.

### FR-07 Failure and recovery

Scope failures to the affected instrument or operation whenever safe. Missing
mandatory evidence blocks new or increased exposure for that instrument.
Unaffected candidates continue; existing positions remain visible. Protective
rules may execute only with valid required inputs. Ledger corruption or other
global critical failures block dependent mutations. Retries and repair rounds
are bounded, and all failures have stable reasons and recovery guidance.

Acceptance: crash/restart, timeouts, invalid model submissions, stale quotes,
missing bars, source conflicts, duplicated commands, and corrupt durable state
have tested outcomes. Resume preserves identities and completed effects. Work
conversation history is not required to reconstruct authoritative progress.

### FR-08 Dashboard and controls

Provide a local dashboard for candidate/policy configuration, run status,
evidence and role outputs, decisions and rejections, orders and fills, positions,
cash/equity, evaluation, and pause controls. Each number links to its authoritative
record. Distinguish current state, frozen historical snapshots, and unavailable
results. The user initiates Work separately in the first release.

Pause blocks new exposure: cancel pending buys that have not yet become effective
and reject new buys or position increases. Research, reductions, exits, protective
stops and accounting reconciliation remain available under their normal evidence,
risk and lifecycle constraints. Pause does not start unattended research.
Persist the pause event and its effective time; it cannot undo earlier simulated
events, including events awaiting catch-up. Resume permits future validated
activity but does not restore cancelled orders automatically.

Acceptance: show one complete cycle and a failure/recovery cycle; reject stale
configuration writes; demonstrate that paused buys are blocked while valid
reductions, exits and protection remain available. Catch-up preserves events
before the pause took effect, and resume does not resurrect cancelled orders.
Display the pause state and its effects on research, pending orders and protection.
No implied dashboard launch feature or unimplemented trading status.

### FR-09 Evaluation

Use forward paper trading and deterministic replay of stored decisions as primary
evidence. Compare against baselines with the same universe, dates, capital,
information availability, costs, and execution assumptions. Measure returns,
drawdown, turnover, costs, risk breaches, evidence quality, operational failures,
and project usage/cost where observable. Record a baseline and evaluation policy
before judging results.

Use one official portfolio valuation at every regular trading session's close,
including sessions without an agent cycle. Catch-up reconstructs each scheduled
daily valuation using its effective-session ledger and prices. Apply the closing
equity definition in FR-06 to the agent and both benchmarks on identical dates.
Label drawdown from this series as daily closing drawdown; it does not measure
intraday declines. Missing inputs follow the availability policy below.

Anchor each evaluation to the positive initial funding value before its first
eligible opening. Each report identifies that starting session and its ending
regular session and retains all intervening scheduled closing observations.
With `E0` as initial capital and `Et` as equity at a scheduled close, use:

| Metric | Definition and required inputs |
| --- | --- |
| Cumulative return | `Et / E0 - 1`; requires initial funding, ending valuation and complete required intervening ledger inputs |
| Daily return | `Et / Eprev - 1`, where `Eprev` is the preceding scheduled closing equity, or `E0` for the first session; unavailable if a required input is missing or the denominator is nonpositive |
| Maximum daily closing drawdown | Largest `1 - Et / peak_to_date` across the interval, where the running peak includes `E0` and all scheduled closing equities through that date; unavailable if any required valuation is missing |

Use these definitions for the agent and both benchmarks. Fees, slippage and
dividends already reflected in equity must not be counted again. Open holdings
retain their closing valuations; evaluation does not require a liquidation trade.

The first release requires two benchmark portfolios:

- A zero-interest cash portfolio that retains its initial capital without trades.
- An equal-weight buy-and-hold portfolio that targets equal initial allocations
  across the predeclared candidate pool, buys whole shares at the first eligible
  opening, retains leftover cash, and holds without scheduled rebalancing.
  Apply the approved corporate-action accounting, including cash dividends
  without automatic reinvestment.

Match the agent's initial capital, evaluation dates, price data, fees, slippage,
settlement and corporate-action assumptions. Freeze the benchmark universe and
portfolio rules before evaluation; candidate-pool or evaluation-configuration
changes must not rewrite earlier benchmark inputs or results. Retain losing and
no-trade periods. A separate deterministic trading-strategy benchmark is deferred.

Keep every scheduled evaluation date, including dates with unavailable portfolio
valuations. Report input coverage and missing-input reasons for the agent and
benchmarks. A metric is unavailable when any of its required inputs is incomplete;
other metrics with complete required inputs may still be reported. Do not fill
gaps with carried-forward prices, silently remove missing dates, or use estimated
valuations to authorize trades. When recovered data changes an evaluation,
create a new revision linked to its input evidence and earlier evaluation;
preserve the earlier record and its original coverage and results.

Historical model experiments are exploratory: a model may know later events,
and historical source publication/revision metadata may be incomplete. Rerunning
model reasoning is not exact replay. Engineering acceptance and observed trading
performance are separate claims; no profitability threshold is invented here.

Acceptance: reconcile evaluation with the ledger, expose unavailable metrics,
replay a stored run without a model or network, and retain adverse/no-trade days.
Verify every regular session has a scheduled closing observation, including days
without model activity, and that daily processing and catch-up produce the same
closing values from identical inputs. Label drawdown with its daily-close basis.
Verify returns against the initial funding and consecutive scheduled closes,
including a loss on the first session; the initial capital participates in the
drawdown peak. A missing previous close or nonpositive daily-return denominator
must not become an invented daily return. Changing capital starts a separate
evaluation without rewriting results or resetting the peak within the old one.
Reproduce both benchmarks from frozen inputs, including integer-share rounding,
leftover cash, costs and supported corporate actions, without using agent proposals
or later candidate-pool membership to change their history. With a missing required
intermediate valuation, report maximum drawdown for that interval as unavailable;
an endpoint-return metric may still be reported when its endpoint valuations and
all other required ledger inputs are complete. Verify that gaps remain visible
and recovered data produces a separately reproducible revision without overwriting
the earlier evaluation. The new scorecard, numerical/input contracts and fixtures
for these definitions, and detailed benchmark contracts require
[RQ-04](research.md#rq-04-evaluation-policy).
Existing research release/shadow gates retain their original semantics.

### FR-10 Durable memory and reflection

Store hypotheses, evidence and counter-evidence, decisions, outcomes, and bounded
reflections with as-of visibility and source/run/version references. Future
cycles can retrieve only memory available at their cutoff. Memory can inform
judgment and propose changes; it cannot silently alter strategy, risk, execution,
or evaluation policy.

Acceptance: prevent future-outcome leakage in replay; distinguish a thesis from
an observation; audit all recommendations and approved policy revisions.

### FR-11 Runtime portability and cost

Use Work as the first external reasoning/orchestration host, behind a versioned
typed boundary. Keep local services and persistence independent of that host.
A standalone API-based model runtime is a later option. Persist runtime/model
provenance where supplied; label unavailable values rather than inventing them.

The incremental project budget is USD 100/month. It excludes an existing ChatGPT
subscription already used for other work; it includes project data, additional
Work credits, future APIs, and any project-driven subscription upgrade difference.
This specification authorizes no purchase. Shared Work allowance may not expose
exact per-cycle spend, so distinguish measurable spend, estimates, and unknown
shared usage. Establish practical stop controls in [RQ-05](research.md#rq-05-cost-and-data-budget).

Acceptance: prove actual host/tool access, structured submission, bounded
delegation and recovery, and document observed cost visibility before claiming
runtime readiness.

### FR-12 Migration and documentation

Reuse validated numerical, normalization, evidence, storage, validation, and
replay foundations. Evolve the old brief into an output view of the new lifecycle.
Necessary interface/MCP changes may be approved during implementation, with
explicit consumer migration and historical artifact compatibility. Do not maintain
two permanent competing product definitions.

Acceptance: one current document per category, no stale authoritative pointers,
requirements-to-task traceability, preserved historical meanings, and a reviewed
compatibility/migration decision for each changed interface.

## Non-goals for the first release

Real-money trading; brokerage accounts/orders; shorting, leverage, derivatives,
crypto or unsupported ETFs; whole-market candidate discovery; high-frequency or
intraday trading; autonomous policy optimization; unbounded agent debate;
multi-user/cloud deployment; direct dashboard-triggered Work runs; and a second
standalone production model runtime.

## Preserved implementation contracts

Until explicitly migrated, the [current compatibility profile](contracts.md#current-implementation-profile)
governs existing research APIs, formulas, source-role gates, resource hashing,
frozen storage, synthesis validation, generic runtime and evaluation APIs. These
details constrain existing consumers and historical interpretation. They do not
silently impose old two-setup, research-only, eleven-operation, or three-skill
limits on the future product. Legacy plan observations must never be relabeled
as simulated fills or realized portfolio performance.
