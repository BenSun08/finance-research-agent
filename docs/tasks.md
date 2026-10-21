# Tasks and Completion Evidence

This is the sole work-status record. [Spec](spec.md) defines requirements;
[plan](plan.md) defines sequence; [checklist](checklist.md) assesses readiness.
Statuses: COMPLETE, IN_PROGRESS, NOT_STARTED, BLOCKED or DEFERRED. Approved
design is not evidence of implemented behavior.

## Current delivery

| ID | Task | Status | Scope and evidence |
| --- | --- | --- | --- |
| D0 | Consolidate approved redesign and repository documentation | COMPLETE | Ten English canonical documents, reference/checker migration, 24 legacy documents and two tracked task reports consolidated; local verification below |
| D0a | Clarify material product ambiguities and shorten project instructions | COMPLETE | Fifteen accepted clarifications recorded in spec across three five-question passes, completed on 2026-10-22. Canonical references and concise AGENTS.md reviewed; final local checks below passed. No further material product clarification is needed before planning. Technical research and all seven implementation-readiness gates remain open. |

D0 contains no production implementation. Existing source, schemas, runtime
skills, workflow, prompts, dependencies and package version must remain unchanged.
The user has confirmed D0; there is no additional documentation approval gate.

## Existing foundation

Presence of the code below establishes implementation inventory, not a new
current CI, installed-host, live-source or personal-graduation claim.

| Area | Implementation status | Evidence |
| --- | --- | --- |
| Provider-neutral model/tool/registry and bounded runtime | IMPLEMENTED | [agent exports](../src/finance_research_agent/agent/__init__.py), [runtime tests](../tests/agent/test_runtime.py) |
| Deterministic market/regime/setup/scoring/sizing and evidence validation | IMPLEMENTED | [domain models](../src/finance_research_agent/domain/models.py), [validation](../src/finance_research_agent/domain/validation.py), compatibility profile |
| Frozen preparation, storage/publication/replay and telemetry | IMPLEMENTED | [preparation](../src/finance_research_agent/application/premarket_preparation.py), [repository](../src/finance_research_agent/adapters/filesystem.py), [replay](../src/finance_research_agent/application/replay_service.py) |
| Typed local MCP operations and three bounded skills | IMPLEMENTED | [operations](../src/finance_research_agent/application/operations.py), [skill index](../skills/README.md) |
| Scenarios, failure evaluation, offline CI, doctor and release evidence machinery | IMPLEMENTED | [release gates](../src/finance_research_agent/evaluation/release_gates.py), [CI](../.github/workflows/ci.yml); baseline includes release-readiness merge `e5df43c` and CI fix `aa3fb0d` |
| Full-source research conformance | DEFERRED | Existing readiness model requires separately approved source scope |
| Actual Work integration, six-role loop, simulator, ledger and dashboard | NOT_STARTED | No production implementation established in D0 |
| Installed-host checks, live smoke and personal elapsed-day graduation | NOT_ESTABLISHED_HERE | Require their own verified/operator evidence; local checks do not substitute |

## Planned implementation

All tasks below are NOT_STARTED. Begin with reviewed bounded feasibility work;
do not infer authorization to implement the full plan from this task list.

| ID | Deliverable | Depends on | Requirements | Completion evidence |
| --- | --- | --- | --- | --- |
| T01 | Work host/transport/subagent and budget spike | D0; RQ-01, RQ-05 | FR-02, FR-03, FR-07, FR-11 | Actual synthetic host trace, structured return, bounded delegation and cross-conversation resume |
| T02 | Source/capability matrix and evidence spike | D0; RQ-02, RQ-05 | FR-01, FR-04, FR-07, FR-11 | Normalized samples, required-role matrix, licensing/availability/cost findings |
| T03 | Simulation, risk, ledger and pause contracts | D0; RQ-03 | FR-01, FR-02, FR-05–FR-08 | Reviewed transition/arithmetic examples, ambiguity/corporate-action/cash rules and idempotency design |
| T04 | Evaluation/baseline and cost policy | D0; RQ-04, RQ-05 | FR-09, FR-11 | Matched baseline definitions, metrics and observation/usage limitations |
| T05 | Versioned cycle/role/decision/portfolio interfaces and first-slice design | T01–T04 | FR-03–FR-07, FR-10–FR-12 | Reviewed schemas, authorization/preconditions, consumer migration and scoped implementation approval |
| T06 | One end-to-end vertical slice | T05 | FR-01, FR-02, FR-05–FR-09 | Known candidate → frozen evidence → Work proposal → risk → simulated order/fill → ledger → replay, including crash/duplicate rejection |
| T07 | Full analyst/challenger/decision loop | T06 | FR-03, FR-04, FR-07 | Six inspectable role outputs, shared context, one supplementary round, invalid/partial/unavailable paths |
| T08 | Portfolio lifecycle and local dashboard | T06; T03 | FR-01, FR-05–FR-08 | Multiple candidates/positions, stops and gaps, accounting/cash, pause/recovery, evidence-linked UI |
| T09 | As-of memory, reflection and evaluation | T07, T08; T04 | FR-09, FR-10 | No future leakage, immutable decisions/outcomes, exact replay and matched forward metrics |
| T10 | Consumer migration and release evidence | T07–T09 | FR-01–FR-12 | Brief/skill integration, compatibility decisions, installed host, replay corpus and forward evidence with explicit limitations |

Use focused tests and review at each task. Update these rows with exact changed
contracts, check results and evidence references as work occurs. Do not copy the
task list into new dated plans or turn research questions into unchecked assumptions.

## Documentation delivery verification

Verified for D0 on 2026-10-16 using Python 3.13.7:

| Check | Result |
| --- | --- |
| Full pytest suite with `FINANCE_RESEARCH_AGENT_OFFLINE_TESTS=1` and the repository network guard | 2,955 passed, 1 live test deselected; one existing `websockets.legacy` deprecation warning |
| `ruff check .` | PASS |
| `mypy src` | PASS; 104 source files |
| Generated-schema drift check | PASS |
| Documentation, internal links, canonical inventory, skills and example checks | PASS |
| Tracked secret scan | PASS; no new potential secrets |
| Ruff formatting of the three changed Python documentation-check files | PASS |
| Final staged diff and whitespace check | PASS; no production source, schema, runtime skill, prompt, dependency or version changes |

The full test run completed after the interactive wait was interrupted. Its
successful completion record was recovered on resume; tracked implementation
and test files were unchanged. Final documentation evidence was then refreshed.
The checker now rejects competing Markdown documents under `docs/` and checks
the saved-task prompt separately from unrelated operational documentation.

These results establish local documentation-delivery verification. They do not
establish remote CI, installed Work integration, live-source smoke, simulated
trading, or personal shadow graduation. The user authorized a local commit;
remote publication and subsequent implementation are outside D0.

D0a clarification verification on 2026-10-19: 58 documentation/workflow pytest
tests passed under the offline network guard; Ruff, mypy (104 source files),
schema export, documentation/link checks and whitespace checks passed. This pass
did not rerun the full suite or establish remote CI, installed-host, live-source
or simulated-trading evidence. The six changed tracked files are documentation
only; the pre-existing untracked Spec Kit installation is untouched.

The first pass asked and answered five questions, with five clarification records
and only the permitted new spec headings. The canonical checklist remains 16/23
checked, with no newly passing items or regressions; all seven implementation
readiness gates remain open. No clarification extension hooks are configured.

The first pass is complete. The user requested a second pass on 2026-10-19,
covering remaining simulation behavior followed by evaluation policy. Its first
two answers, accepted on 2026-10-20, define daily-data stops and single-opening,
full-quantity ordinary orders in FR-06, including unresolved historical data.
Its third answer, accepted on 2026-10-21, adds ordinary split/cash-dividend
accounting with raw execution/valuation prices and unresolved-event handling.
Its fourth answer, accepted on 2026-10-21, selects cash and equal-weight
buy-and-hold benchmarks, with a separate trading-strategy benchmark deferred.
Its fifth answer, accepted on 2026-10-21, preserves missing valuation dates and
coverage, makes availability depend on each metric's required inputs, and creates
a new evaluation revision when data is recovered. This completes the second
five-question pass; ten distinct answers are recorded across both passes.
At the end of the second pass, remaining [simulation](research.md#rq-03-simulation-semantics)
and [evaluation](research.md#rq-04-evaluation-policy) decisions included simultaneous
ordinary exit/stop ordering, cancellation/replacement behavior, cost-basis and
valuation conventions, and evaluation formulas/observation intervals. These could
change fills or reported results, so the conditional local commit was withheld
at that stage. The third pass below addresses those remaining product choices.

Second-pass verification on 2026-10-21: all 58 documentation/workflow pytest
tests passed under the offline network guard. Ruff, mypy (104 source files),
schema export, documentation/link checks and whitespace checks passed. Final
diff review confirmed six tracked Markdown files changed, with no production,
schema, dependency or version changes. Structural checks confirmed ten unique
clarification records, five per invoked pass, and only the permitted new spec
headings. The canonical checklist remains 16/23 checked, with no newly passing
items or regressions; all seven implementation-readiness gates remain open.
No clarification extension hooks are configured. The full suite was not rerun;
these checks establish no remote CI, installed-host, live-source or simulated-
trading evidence.

The user requested a third pass on 2026-10-21, focused on the remaining execution,
accounting and evaluation definitions before planning. Its first answer, accepted
on 2026-10-22, gives an opening-triggered protective stop priority over an
overlapping ordinary sell, with one full-position stop fill and a superseded
ordinary order. A stop triggered only later by the daily low applies to shares
remaining after opening sells. FR-06, the data model and RQ-03 are aligned.
Its second answer, accepted on 2026-10-22, keeps the original instruction active
when replacement validation fails and applies a successful replacement together
with the original's cancellation. Ordinary changes must precede the intended
opening; protective-stop changes preserve protection until their future effective
session. Explicit cancel-only requests retain timing and safety checks. FR-06,
the data model and RQ-03 are aligned.
Its third answer, accepted on 2026-10-22, selects weighted-average position cost,
including purchase fees in acquisition cost and deducting sale fees from proceeds,
with no duplicate slippage deduction and separate ordinary-dividend income.
FR-06 includes the approved arithmetic example; the data model and RQ-03 are
aligned.
Its fourth answer, accepted on 2026-10-22, selects every regular session's closing
valuation, including sessions without agent activity and catch-up. Equity includes
settled cash, unsettled sale proceeds, outstanding dividend receivables and shares
marked at unadjusted closing prices; unrealized trading results deduct remaining
acquisition cost. FR-06/FR-09, the data model and research references are aligned,
with identical benchmark observation dates and an explicit daily closing drawdown
label.
Its fifth answer, accepted on 2026-10-22, fixes each evaluation's initial funding,
excludes later external deposits/withdrawals, and defines cumulative/daily returns
and maximum daily closing drawdown. A capital change starts an independent
portfolio/evaluation while data recovery creates a linked revision of the existing
evaluation. FR-01/FR-09, the data model and RQ-04 are aligned. All five questions
in this pass are answered.

Final D0a review and verification on 2026-10-22: 58 documentation/workflow pytest
tests passed under the offline network guard; Ruff, mypy (104 source files),
schema export, documentation/link checks, whitespace checks and the tracked-file
secret scan passed. Arithmetic spot-checks confirmed the weighted-average example
and the initial-capital return/drawdown definitions. Structural checks confirmed
15 unique clarification records, five per invoked pass, and only permitted new
spec headings. The final diff contains six tracked Markdown files; production
code, tests, schemas, dependencies, runtime skills and package version are unchanged.
The pre-existing untracked Spec Kit installation is excluded from this delivery.

The consistency review scoped the old same-session-fill prohibition to ordinary
orders, preserving the approved same-day protective stop, and made approved split
adjustments explicit in execution quantities. No further material product question
blocks planning. Detailed event schedules, numerical precision/rounding, versioned
contracts and fixtures, auxiliary scorecard metrics, and actual host/source/budget
evidence remain in T01–T05 and RQ-01–RQ-05. These are technical design and readiness
work; this clarification completion does not close those gates.

The canonical checklist remains 16/23 checked, with no newly passing items or
regressions; all seven implementation-readiness items remain open. No clarification
extension hooks are configured. The full test suite was not rerun in D0a; local
documentation checks establish no remote CI, installed-host, live-source or
simulated-trading evidence. The user's conditional authorization now permits the
local documentation commit. Remote publication and production implementation
remain outside this delivery. Next workflow: speckit-plan, updating canonical
documents in place while preserving the accepted product choices.
