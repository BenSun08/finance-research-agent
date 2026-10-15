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
   against eligible market data, records costs and portfolio value, and presents
   hold, reduce, exit, buy, and no-trade decisions with their evidence.
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

Acceptance: reject unsupported instruments, shorts, fractional-share proposals,
and leverage; preserve policy identity and the initial funding event; demonstrate
cash and position conservation through a complete synthetic lifecycle. Settle
the cash-availability rules in [RQ-03](research.md#rq-03-simulation-semantics)
before implementing accounting.

### FR-02 Cadence and bounded cycles

Run daily after the regular close, with decisions eligible for execution no
earlier than the next trading session. The intended holding horizon is days to
weeks. The user manually starts cycles in Work; unattended scheduling and direct
dashboard-to-Work launch are not first-release prerequisites. Every cycle has
explicit identity, stages, cutoff, deadline, tool/research limits, and terminal
outcomes. Exact limits must be versioned and resolved in the runtime spike.

Acceptance: a same-session or pre-decision fill is impossible; calendar holidays,
late starts, interrupted cycles, duplicate requests, and exhaustion have explicit
tested outcomes. A bounded no-trade result completes a cycle.

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

Execute ordinary next-session buys/sells and protective stops locally using
versioned, documented assumptions. Include configured fees and slippage. A gap
can fill beyond a stop price; a stop is not a guaranteed price. Maintain an
auditable order/fill/cash/position/equity lifecycle with idempotent commands.
Simulated execution is visibly labeled throughout artifacts and the dashboard.

Acceptance: exact replay reproduces fills and ledger from the same accepted
decisions, data, and simulator version; no duplicate application is possible;
conservation and reconciliation hold. Before readiness, resolve insufficient
cash/gaps, settlement, partial/missing data, expiry/cancellation/replacement,
daily-bar ambiguity, corporate actions, price adjustment, and valuation under
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

Acceptance: show one complete cycle and a failure/recovery cycle; reject stale
configuration writes; explain pause effects on research, pending orders, and
protective processing according to the resolved lifecycle contract. No implied
dashboard launch feature or unimplemented trading status.

### FR-09 Evaluation

Use forward paper trading and deterministic replay of stored decisions as primary
evidence. Compare against baselines with the same universe, dates, capital,
information availability, costs, and execution assumptions. Measure returns,
drawdown, turnover, costs, risk breaches, evidence quality, operational failures,
and project usage/cost where observable. Record a baseline and evaluation policy
before judging results.

Historical model experiments are exploratory: a model may know later events,
and historical source publication/revision metadata may be incomplete. Rerunning
model reasoning is not exact replay. Engineering acceptance and observed trading
performance are separate claims; no profitability threshold is invented here.

Acceptance: reconcile evaluation with the ledger, expose unavailable metrics,
replay a stored run without a model or network, and retain adverse/no-trade days.
The new scorecard and baselines require [RQ-04](research.md#rq-04-evaluation-policy).
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
