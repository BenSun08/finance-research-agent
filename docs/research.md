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

No item below is resolved by prose or by the user's general product approval.
Each needs a bounded investigation, recorded evidence, a technical decision in
the owning document, and review of the dependent implementation scope.

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
Specify settlement/cash availability, gap and insufficient-cash behavior, order
expiry/cancel/replace, daily-bar ambiguous sequencing, partial fills if supported,
halts/missing bars, splits/dividends, adjusted versus raw prices, cost basis,
realized/unrealized PnL and valuation. Decide deterministic conservative handling
where daily data cannot reveal intraday order. Define pause and restart behavior
for pending/protective orders. Resolve explicit numerical risk/portfolio controls;
do not turn old analytical-sizing defaults into account policy.

Exit: reviewed state tables, arithmetic examples and failure scenarios in
data-model/contracts, including conservation/idempotency invariants. Dependent
tasks: T03, T05, T06, T08.

### RQ-04 Evaluation policy

Define matched passive/simple-rule baselines, observation interval, return and
drawdown denominators, treatment of cash/no-trade/missing days, fees/slippage,
corporate actions and incomplete valuations. Separate engineering release gates,
forward observations and research hypotheses. Select measurable evidence-quality,
operational and cost metrics. Document historical-model leakage and source
revision limitations; no invented alpha acceptance threshold or cherry-picked
sample. Preserve the current research shadow scorecard as a distinct version.

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
