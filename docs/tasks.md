# Tasks and Completion Evidence

This is the sole work-status record. [Spec](spec.md) defines requirements;
[plan](plan.md) defines sequence; [checklist](checklist.md) assesses readiness.
Statuses: COMPLETE, IN_PROGRESS, NOT_STARTED, BLOCKED or DEFERRED. Approved
design is not evidence of implemented behavior.

## Current delivery

| ID | Task | Status | Scope and evidence |
| --- | --- | --- | --- |
| D0 | Consolidate approved redesign and repository documentation | COMPLETE | Ten English canonical documents, reference/checker migration, 24 legacy documents and two tracked task reports consolidated; local verification below |

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
