# Research and Decision Record

This is the sole research/decision record. Findings below inform [plan](plan.md);
requirements remain in [spec](spec.md). External references were reviewed during
the redesign discussion on 2026-10-14. Upstream branches and product availability
can change; the actual environment spike remains required.

## Reference findings

| Reference | Finding and adopted implication |
| --- | --- |
| [TradingAgents graph](https://github.com/TauricResearch/TradingAgents/blob/main/tradingagents/graph/setup.py) | Provides analyst, research debate, trader and risk/portfolio decision stages. Adopt explicit role outputs and bounded challenge; do not assume its exact graph or dependencies are necessary here. |
| [TradingAgents repository](https://github.com/TauricResearch/TradingAgents) | The inspected decision/backtest path is useful for agent decision research; it does not establish the cash, quantity, fill and persistent portfolio accounting contract required by this project. Evaluate execution claims against actual code and evidence. |
| [Claude trading skills workflows](https://github.com/tradermonty/claude-trading-skills/blob/main/workflows/README.md) | Reusable skills and documented workflows are useful tools. The inspected swing workflow retains human brokerage action; a skills collection alone does not provide this project's persistent simulated decision loop. |
| [Spec Kit](https://github.com/github/spec-kit), [plan template](https://github.com/github/spec-kit/blob/main/templates/plan-template.md) | Distinguishes constitution, spec, plan, tasks and supporting research/data-model/contracts/quickstart/checklists. Adopt these responsibilities as one project-wide file each. This is a deliberate adaptation of its feature-directory convention, not a claim of unmodified CLI compatibility. |
| [Work subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) | Work supports delegated agents subject to host/account configuration. Distinct roles do not guarantee six concurrent workers or six separate models. Validate real tool inheritance and bounded handoffs. |
| [Work plugins](https://learn.chatgpt.com/docs/plugins), [Work overview](https://learn.chatgpt.com/docs/enterprise/chatgpt-work-overview) | Plugin/runtime availability and local connectivity depend on environment. Existing local Codex plugin files do not prove Work compatibility, and cloud execution cannot assume access to this computer's local services. |
| [OpenAI plugin development](https://developers.openai.com/plugins/build/plugins) | Manifest and runtime conventions evolve. Inspect the actual installed host and preserve current plugin bytes until the compatibility spike selects a reviewed integration. |
| [Workspace-agent API trigger](https://developers.openai.com/cookbook/examples/chatgpt/workspace_agents/workspace-agents-api-trigger) | A workspace-agent API channel has its own provisioning/access contract. It is not evidence that an ordinary Work conversation can be launched as a model API or that the local dashboard can obtain a synchronous final result. |
| [Work administration and usage](https://learn.chatgpt.com/docs/enterprise/work-admin-faq#how-does-chatgpt-work-usage-translate-into-spend-over-time) | Shared allowance/credits and account policies affect availability and spend. Treat exact per-cycle Work cost as unknown unless the environment actually exposes it. |
| [SEC T+1 investor bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin) | Reviewed on 2026-10-19: applicable U.S. stock and ETF transactions settle one business day after the trade. FR-01 adopts T+1 for the simulator; restricting buy fills to settled cash is a separately approved product policy, not a claim that the bulletin mandates that restriction for every brokerage account. |
| [SEC stock-split glossary](https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-split), [dividend entitlement](https://www.investor.gov/introduction-investing/investing-basics/glossary/ex-dividend-dates-when-are-you-entitled-stock-and), [reverse splits](https://www.investor.gov/introduction-investing/investing-basics/glossary/reverse-stock-splits) | Reviewed on 2026-10-20: splits change share quantities; dividend entitlement and payment dates are distinct; reverse splits can involve cash in lieu of fractional shares. The simulator's supported-event scope, raw-price accounting and unresolved-event policy were approved on 2026-10-21 in FR-06. Verify event-specific terms rather than infer missing cash entitlements. |

## Approved decisions

The user confirmed the target and the documentation pass after the design
interview. These decisions replace conflicting old product direction:

| Decision | Owning requirement |
| --- | --- |
| Trading decisions plus local paper portfolio, no real execution | FR-01, FR-05, FR-06 |
| After-close, next-session, long-only USD integer-share cash account | FR-01, FR-02, FR-06 |
| Known candidate pool first; discovery later | FR-01 |
| Six roles, one bounded supplementary round, Work first | FR-03, FR-11 |
| Agent judgment may go beyond the old two technical setup families | FR-04 |
| User starts a cycle; validated simulation needs no per-order approval | Constitution; FR-02, FR-05 |
| Local dashboard and durable local state; manual Work start | FR-07, FR-08 |
| Forward paper evaluation, exact stored-decision replay, as-of memory | FR-09, FR-10 |
| USD 100 incremental monthly budget; no presumed exact shared Work usage | FR-11 |
| Reuse core; permit reviewed interface migrations; one canonical file per category | FR-12 |

## Implementation gates

The 2026-10-22–23 planning pass resolves the desk-design questions in the decision
register below. Readiness still requires the listed observations, executable
fixtures and reviews. A proposed technical choice is not a claim of actual
host access, approved source entitlement or implemented simulation. Missing
external evidence is a blocking result for its dependent implementation, not an
invitation to fill it with an assumption.

## Technical decisions from planning

All selections in this table are proposed technical design for T01–T05 review.
They preserve the accepted product policies; [contracts](contracts.md#target-interface-boundary)
and [data model](data-model.md#versioned-target-records) own their detailed rules.

| Question | Decision | Rationale | Alternatives considered |
| --- | --- | --- | --- |
| Atomic account mutations | Separate local SQLite trading repository; immutable event/artifact bytes plus receipt/version/projection in one transaction | Replacement and fill/accounting must never commit partially | Extend the research file store with a new cross-file journal; server database; both add recovery/deployment work |
| Host authority | Separate `trading.v1` family, trusted actor binding, expected versions and idempotency receipts | Structured model output remains untrusted; capability discovery is not permission | Reinterpret publication as execution; host-controlled ledger writes; rejected |
| Calendar/time | Separate frozen trading and settlement calendars; chronological effective phases with distinct processing time | T+1 is not the next exchange session; catch-up must reproduce original opportunities | Add 24 hours; use XNYS alone; process by retrieval time; rejected |
| Price/evidence | Explicit RAW execution/valuation identity and normalized opening eligibility | Existing research default is adjusted; daily OHLC alone does not establish an executable opening | Change the legacy default; silently treat first later trade as opening; rejected |
| Numeric/accounting | Explicit 34-digit Decimal profile, cent postings, eight-place modeled price, residual-cost conservation, exact split-stop comparisons | Determinism independent of ambient context; complete exits leave no cost residue | Binary floats; repeated average-cost rounding; unversioned display rounding; rejected |
| Order conflict/priority | One ordinary instruction and one whole-position stop per instrument in the first profile; frozen action priority | Prevent double-selling and nondeterministic cash competition | Net conflicting actions or order by response arrival; rejected; T05 reviews the proposed restrictions |
| Evaluation | Separate trading scorecard, scheduled-close coverage, per-metric availability and preserved recovery revisions | Existing research scorecard has non-P&L semantics | Relabel PlanObservation/ShadowScorecard as trading results; drop missing days; rejected |
| Buy-and-hold allocation | Cost-inclusive equal sleeves, whole shares, no residual redistribution, frozen first-eligible-opening selector | Deterministic approved equal allocation with leftover cash and no hindsight | Redistribute rounding cash, skip unknown eligibility or choose start from returns; rejected |
| Cost controls | Finite local workload policy and explicit measured/estimated/unobservable cost | Shared Work usage may lack per-cycle attribution | Infer dollars from tokens/subscription share or introduce paid API for billing visibility; rejected |

Primary references checked on 2026-10-22:

- [Python SQLite transaction control](https://docs.python.org/3.12/library/sqlite3.html#transaction-control)
  and [SQLite atomic transactions](https://www.sqlite.org/transactional.html)
  support a local transaction boundary. This does not prove the proposed adapter
  or filesystem durability; fault and recovery tests remain mandatory.
- [Python Decimal](https://docs.python.org/3.12/library/decimal.html) exposes
  precision, exponents, rounding and traps. The selected numeric values are a
  simulator design choice requiring review, not a Python default or market rule.
- [Work surfaces](https://learn.chatgpt.com/docs/use-chatgpt),
  [plugins](https://learn.chatgpt.com/docs/plugins) and
  [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) describe
  conditional local/cloud execution and delegated tools. They do not establish
  this account's plugin installation, local stdio inheritance or six workers.
- [Alpaca historical bars](https://docs.alpaca.markets/us/reference/stockbars)
  distinguishes adjustment modes and symbol-first pagination. Validate all pages,
  symbols and session coverage; one successful response is insufficient.
  [Bar aggregation details](https://docs.alpaca.markets/us/docs/market-data-faq)
  also require feed/condition semantics rather than assuming an official auction.
- [Alpaca subscriptions](https://docs.alpaca.markets/us/docs/about-market-data-api)
  lists Basic as free with IEX real-time coverage, a recent historical-data
  restriction and 200 historical calls/minute; Algo Trader Plus is quoted at
  USD 99/month. These are public quotes, not account entitlements or purchases.
  The paid quote alone nearly consumes the USD 100 ceiling, before other costs.
- The SEC [T+1](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin)
  and [dividend entitlement](https://www.investor.gov/introduction-investing/investing-basics/glossary/ex-dividend-dates-when-are-you-entitled-stock-and)
  references support the already accepted policy context. Actual calendar and
  event-specific terms still need verified source coverage; special distributions
  cannot be inferred from the ordinary-dividend rule.

### Source and capability matrix

This is the proposed required-evidence matrix and inspected code inventory.
It does not approve a provider or claim current full-source availability.

| Action/capability | Required evidence | Current inventory and remaining proof |
| --- | --- | --- |
| New/increased common-stock exposure | Identity/eligibility, raw market/session data, shared market/macro, company fundamentals, material news/official events, applicable corporate actions and reconciled risk state | Only market/calendar operationally composed; fundamentals/events/actions samples, availability and policy approval missing |
| New/increased ETF exposure | Above market/risk/event basis plus issuer, non-leveraged/non-inverse classification, structure/exposures appropriate to the ETF | No demonstrated ETF analysis source; define field coverage/freshness and provide normalized samples |
| Ordinary reduction/exit | Valid held quantity/order, raw eligible opening, applicable actions, execution/settlement calendars | Market normalization reusable; target opening eligibility and calendar/event coverage unproved; unrelated buy-thesis fundamentals are not an automatic exit prerequisite |
| Protective stop | Valid position/stop, raw open/low, regular session, applicable adjustments | Target stop path absent; uncertain prices/events block the affected protection outcome |
| Closing valuation | Valid effective ledger, raw close for held assets, cash/receivables, event and calendar completeness | Target ledger/valuation absent; missing unheld-asset marks alone do not invalidate equity |
| Company fundamentals | Financial values with period/unit/publication/revision provenance, authoritative filings/company sources | SEC adapter supplies filing metadata; metadata alone is not a financial analysis input |
| News and shared macro | Permitted excerpts/metadata, authority classification, event/publication/revision/retrieval times and source coverage | Discovery/IR/macro adapters exist but are unwired; an empty response does not prove no material events |
| Splits and dividends | Stable instrument/event ID, revision, verified ratios/amounts, effective/entitlement/payment terms and completeness | No inspected target corporate-action adapter or approved source entitlement |
| Settlement | Covered business-date calendar, provenance/version and verified holidays | Existing XNYS adapter supplies trading sessions only |

Each approved source row must add provider/API/feed, required/optional fields,
maximum age, historical availability semantics, correction/pagination/rate bounds,
license/storage permission, configured-account access, quote date, sample hash,
and exact failure scope. Require evidence of adequate coverage for
NO_EVENTS_CONFIRMED; otherwise report unknown. Live outcome evidence collected
later may reconcile an old accepted instruction but cannot enter that decision's
frozen research inputs. Licensing and event-source selection remain RQ-02 work.

### Budget and feasibility evidence

Freeze maximum candidates, role assignments/concurrency, tool/provider attempts,
submission/repair attempts, bytes, output sizes and elapsed deadline. One
supplementary round is the product limit; restart must not reset counters.
The proposed synthetic T01 profile uses one candidate, sequential logical roles,
one supplementary round, at most two submissions per assignment, zero live
provider requests and no paid fallback. T01 must choose and record the finite
deadline/payload/tool-call bounds before running, then measure them; these are
spike controls, not unreviewed production defaults.

| Incremental item | Current planning evidence | Required readiness evidence |
| --- | --- | --- |
| Existing ChatGPT subscription | Excluded baseline under FR-11 | Identify project-driven upgrade delta separately |
| Work credits/upgrade delta | No purchase or account-specific rate established | Account visibility, incremental quote/allocation and supported stop controls |
| Market data | Public Basic/Plus quote above | Actual permitted feed, delayed-data suitability, storage terms and selected cost |
| Fundamentals/ETF/news/events/actions/calendar | No complete approved source bundle | Coverage/license inventory and total quoted incremental cost |
| Model APIs | Unselected | Separate architecture/purchase decision if later proposed |
| Shared Work allowance | Per-cycle dollar attribution not established | Report observable units and unknown allocation explicitly |

For measurable charges, stop new spending work before the approved monthly
ceiling would be exceeded; do not silently buy credits. Keep accounting/recovery
available with valid stored inputs. Practical workload caps cannot prove a hard
dollar ceiling for unobservable usage. RQ-05 requires a complete dated inventory,
uncertainty/tax/term notes, measured workload and operator-visible controls.

### Remaining gate evidence

| Gate | Designed here | Required before closure |
| --- | --- | --- |
| RQ-01 / T01 | Host envelope, authority, receipts, role/resume semantics | Actual Work query and idempotent command trace, invalid/duplicate/lost-response handling, delegation/tool inheritance, cancellation and new-conversation resume |
| RQ-02 / T02 | Action-specific matrix, raw-price and provenance rules | Approved source rows, normalized permitted samples, licenses, configured coverage and complete cost fit |
| RQ-03 / T03 | Event/state tables, precision, cash/cost rules and S01–S13 oracles | Review of proposed numeric/conflict rules, calendar/event evidence, executable adversarial fixtures and explicit approved risk/cost parameters |
| RQ-04 / T04 | Frozen evaluation/baselines, availability matrix and E01–E06 oracles | Reviewed benchmark selector/numeric/metric versions and executable replay/recovery fixtures |
| RQ-05 / T01–T04 | Cost inventory structure and bounded synthetic workload | Actual account visibility, full quotes, measured limits and practical stop controls |
| T05 contract readiness | Versioned records, operations, migration and proposed file boundaries | Strict generated schemas, consumer fixtures, reviewed adapter/transaction scope and first-slice approval |

The detailed investigation criteria below remain exit obligations; the new
design sections supply their proposed answers rather than marking them passed.

### RQ-01 Work integration

Determine the actual Work account/runtime, plugin installation route, local
transport/connectivity, available subagent behavior, tool access inheritance,
structured-output submission, cancellation/budget signals and resume behavior.
Inspect a real installed plugin invocation using synthetic data and a disposable
run. Demonstrate one query and one narrowly scoped idempotent local command,
plus invalid submission and interrupted-conversation recovery. Record observed
model/runtime metadata and what remains unavailable. Do not add a paid API
fallback or claim dashboard launch support by inference.

Exit: an observed trace and a reviewed typed host adapter contract; otherwise a
clear blocker and alternative requiring an explicit decision. Dependent tasks:
T01, T05, T06.

### RQ-02 Required evidence and sources

Define required evidence by instrument/action, including ETF-specific needs and
existing-position protection. Inventory market, company/fundamental, news,
official events, macro calendar and corporate-action sources. Check point-in-time
availability, coverage/feed limits, licenses, retrieval bounds, provenance and
price. Existing adapter classes are not proof of configured/approved sources.
Use available existing providers first where they satisfy requirements; additional
sources need reviewed adapters and source policies.

Exit: a concrete source/capability matrix with unavailable cases and scoped
failure behavior, sample normalized evidence and a budget fit. No live collection
or subscription purchase is authorized by D0. Dependent tasks: T02, T05, T07.

### RQ-03 Simulation semantics

Resolve calendar/session eligibility and authoritative order of reconciliation,
corporate actions, pending orders, protective stops, fees, marks and decisions.
Define settlement-calendar and cash-availability transitions for the approved
T+1 policy in [FR-01](spec.md#fr-01-instruments-and-portfolio), and arithmetic
examples for the opening-price execution, daily-data stops and buy rejection rules
in [FR-06](spec.md#fr-06-simulated-execution-and-accounting), including its
single-opening/full-quantity ordinary-order policy and distinction between
confirmed expiry and missing-data uncertainty. Derive transition and recovery
examples for the approved replacement policy: preserve the original on validation
failure, apply both changes together, enforce opening cutoffs despite delayed
reconciliation, and retain protection until a stop replacement takes effect.
Include separate cancel-only requests under the same timing and safety checks.
Define halt/missing-bar evidence and derive closing-valuation fixtures from the
approved FR-06 equity/unrealized-result definitions, including settlement and
dividend components without double counting. Contract the approved
weighted-average position-cost policy, purchase-fee capitalization, sale-fee
deduction and separate dividend income. Define Decimal precision/rounding and
cost-conservation examples for additions, reductions, full exits and splits;
derive realized/unrealized results without deducting modeled slippage twice.
Contract the approved opening-stop priority over overlapping ordinary sells,
including supersession records and residual-position protection when only the
later daily low triggers a stop; do not re-open that ordering choice.
Contract the approved raw-price corporate-action policy with event-boundary
examples for splits, dividend receivables/payments,
fractional remainders and unsupported or incomplete events. Do not re-open the
approved common-event scope or count corporate actions through both adjusted prices
and ledger events.
Preserve the approved daily-data stop assumptions and event-time precision when
defining transitions. Translate the approved restart
behavior in [FR-02](spec.md#fr-02-cadence-and-bounded-cycles) and pause behavior in
[FR-08](spec.md#fr-08-dashboard-and-controls) into exact transition and effective-time
rules for pending/protective orders. Resolve explicit numerical risk/portfolio
controls; do not turn old analytical-sizing defaults into account policy.

Exit: reviewed state tables, arithmetic examples and failure scenarios in
data-model/contracts, including conservation/idempotency invariants. Dependent
tasks: T03, T05, T06, T08.

### RQ-04 Evaluation policy

Translate the approved cash and equal-weight buy-and-hold benchmarks in
[FR-09](spec.md#fr-09-evaluation) into frozen input and arithmetic contracts,
including whole-share allocation, leftover cash and costs. A separate simple-rule
trading benchmark is deferred. Contract the approved fixed-capital evaluation
boundary and FR-09 return/drawdown formulas, including the initial-funding peak,
consecutive scheduled-close references, nonpositive daily-return denominators
and an independent portfolio/evaluation when starting capital changes. Build
fixtures for cash/no-trade days, fees/slippage and corporate actions without
double counting equity components. Use the approved regular-session closing
observations for both agent and benchmarks, including sessions with no agent
activity and catch-up; retain the daily closing drawdown label without implying
intraday coverage.
Contract the approved missing-valuation policy in FR-09: retain scheduled dates
and coverage, define each metric's required inputs and unavailable reasons, and
preserve prior evaluations when recovered data creates a new revision. Include
fixtures where complete endpoint inputs permit a return metric while a missing
required intermediate valuation prevents drawdown reporting; do not substitute
carried-forward prices or silently remove gaps. Separate engineering release gates,
forward observations and research hypotheses. Select measurable evidence-quality,
operational and cost metrics. Document historical-model leakage and source
revision limitations; no invented alpha acceptance threshold or cherry-picked
sample. Specify auxiliary turnover, cost and quality-metric calculations in the
technical scorecard design. Preserve the current research shadow scorecard as a
distinct version.

Exit: approved evaluation inputs/formulas/baselines and initial replay fixtures.
Dependent tasks: T04, T09, T10.

### RQ-05 Cost and data budget

Build the incremental cost inventory against USD 100/month, separating recurring
data, extra Work credits, upgrade delta, later API spend and unobservable shared
allowance. Determine supported alerts and stop controls, expected candidate/role
workload and bounded retries. Exact token-to-dollar accounting is conditional on
host support. No purchase or subscription upgrade follows from this record.

Exit: an observed/quoted budget with uncertainty and practical limits. Dependent
tasks: T01, T02, T04.

## Consolidation provenance

The pre-consolidation snapshot is Git commit `d4915fb`. Retired paths below are
historical references, not live documents. Recover exact original text with
`git show d4915fb:<path>`. Their valid semantics are merged by responsibility;
stale example signatures and unchecked implementation lists are not restored as
current facts. The user's new one-file-per-category rule supersedes the former
practice of retaining dated specs/plans in the working tree.

| Retired path | Current home and disposition |
| --- | --- |
| `docs/architecture/v0.1-boundaries.md` | constitution.md, plan.md and contracts.md; authority preserved, target updated |
| `docs/methodology/regime-and-setups.md` | contracts.md; exact current setup/scoring/ranking profile retained |
| `docs/operations/release-readiness.md` | operations.md and quickstart.md; current procedures retained |
| `docs/operations/scheduling-and-recovery.md` | operations.md and quickstart.md; current procedures retained |
| `docs/superpowers/plans/2026-08-20-ai-market-research-agent-premarket-v0.1.md` | spec.md and plan.md replace product/roadmap; contracts.md retains implemented semantics; tasks.md replaces old execution lists |
| `docs/superpowers/plans/2026-08-31-product-a-skill-workflow-contract-delta.md` | contracts.md and operations.md; current typed boundary/resource identity retained; future host direction in plan.md |
| `docs/superpowers/plans/2026-09-09-v04-slice-4b-replay-contracts.md` | contracts.md; exact temporal and historical replay semantics retained |
| `docs/superpowers/plans/2026-09-15-roadmap-reconciliation.md` | spec.md and plan.md replace product/roadmap; contracts.md retains implemented semantics; tasks.md replaces old execution lists |
| `docs/superpowers/plans/2026-09-16-product-a-spec-conformance-refactor.md` | contracts.md, operations.md and tasks.md; current invariants/evaluation retained, stale checkboxes replaced by inspected status |
| `docs/superpowers/plans/2026-09-28-product-a-run-initialization.md` | contracts.md and data-model.md; allocation/freeze/resume/deadline corrections retained |
| `docs/superpowers/plans/2026-09-28-r9-evidence-cutoff-lifecycle.md` | contracts.md and data-model.md; allocation/freeze/resume/deadline corrections retained |
| `docs/superpowers/plans/2026-09-28-r9-source-role-quality.md` | contracts.md; frozen required roles and scoped unavailability retained |
| `docs/superpowers/plans/2026-09-29-product-a-performance-telemetry.md` | contracts.md; counters, checkpoint semantics and target/deadline distinction retained |
| `docs/superpowers/plans/2026-09-29-product-a-r10-boundary-plan.md` | contracts.md and operations.md; current typed boundary/resource identity retained; future host direction in plan.md |
| `docs/superpowers/plans/2026-10-08-product-a-preparation-handoff-evidence.md` | contracts.md and data-model.md; allocation/freeze/resume/deadline corrections retained |
| `docs/superpowers/specs/2026-08-19-ai-market-research-agent-premarket-design.md` | spec.md and plan.md replace product/roadmap; contracts.md retains implemented semantics; tasks.md replaces old execution lists |
| `docs/superpowers/specs/2026-08-31-product-a-skill-workflow-contract-delta.md` | contracts.md and operations.md; current typed boundary/resource identity retained; future host direction in plan.md |
| `docs/superpowers/specs/2026-09-15-roadmap-reconciliation-design.md` | spec.md and plan.md replace product/roadmap; contracts.md retains implemented semantics; tasks.md replaces old execution lists |
| `docs/superpowers/specs/2026-09-28-product-a-run-initialization-design.md` | contracts.md and data-model.md; allocation/freeze/resume/deadline corrections retained |
| `docs/superpowers/specs/2026-09-28-r9-source-role-quality-design.md` | contracts.md; frozen required roles and scoped unavailability retained |
| `docs/superpowers/specs/2026-09-29-product-a-performance-telemetry-design.md` | contracts.md; counters, checkpoint semantics and target/deadline distinction retained |
| `docs/superpowers/specs/2026-09-29-product-a-tool-and-orchestration-boundary-design.md` | contracts.md and operations.md; current typed boundary/resource identity retained; future host direction in plan.md |
| `docs/superpowers/specs/2026-10-08-product-a-preparation-handoff-correction.md` | contracts.md and data-model.md; allocation/freeze/resume/deadline corrections retained |
| `docs/superpowers/specs/2026-10-10-product-a-r11-evaluation-release-readiness-design.md` | contracts.md, operations.md and tasks.md; current invariants/evaluation retained, stale checkboxes replaced by inspected status |
| `.superpowers/sdd/2026-09-16-product-a-spec-conformance-refactor/task-r3-3-report.md` | contracts.md; security/storage invariants retained; historical review evidence stays in Git |
| `.superpowers/sdd/2026-09-16-product-a-spec-conformance-refactor/task-r3-5-report.md` | contracts.md; security/storage invariants retained; historical review evidence stays in Git |

Later corrections take precedence within the preserved profile: the Oct-08
application preparation boundary may return an already published immutable run,
although the lower allocation contract rejects allocating that published revision;
telemetry's 900,000 ms target is distinct from the preparation service's actual
trusted deadline; the packet budget is 8,000,000 bytes, with artifact arrays up to
8,192 and model/evidence arrays still bounded at 128. Existing R11 code and
verified receipts determine status, not stale master-plan checkboxes.
