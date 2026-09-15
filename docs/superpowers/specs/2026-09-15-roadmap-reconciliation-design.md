# Product A Roadmap Reconciliation Design

Date: 2026-09-15
Status: Approved forward-looking architecture reconciliation

## Purpose

This document reconciles three legitimate views of the repository:

1. the historical Product A design and master implementation blueprint;
2. the implementation that actually landed from v0.1 through v0.5; and
3. the forward sequence required to finish Product A without transferring
   deterministic business authority to a model or generic agent loop.

Historical plans remain historical design records. They must not be rewritten as
though they had predicted the repository's eventual release sequence. This design
supplies the forward-looking bridge and does not replace their detailed Product A
requirements.

## Current Verified State

The actual v0.1→v0.5 implementation is accepted as a valid architectural
foundation.

| Milestone | Verified repository capability | Status |
| --- | --- | --- |
| v0.1 | Deterministic research core and the initial market-regime skill | Complete |
| v0.2 | Provider-neutral historical-data contracts and market-data-only Alpaca adapter | Complete |
| v0.3 | Provider-independent deterministic regime workflow and fetcher port | Complete |
| v0.4 | Frozen evaluation, benchmark identity, replay, walk-forward, and regression-gate contracts | Complete |
| v0.5 | Provider-neutral model/tool contracts, assistant actions, deterministic registry and fakes, and bounded runtime through Slice 3B | Complete through Slice 3B |

The v0.5 foundation landed through PRs #19–#22 in this order:

1. `ModelPort` and model contracts;
2. tool contracts, `ToolPort`, and `ToolRegistry`;
3. `FinalAnswer`, `ToolCall`, and `ToolObservation`; and
4. the minimal synchronous `AgentRuntime`.

There is no production model provider, Product A domain-tool adapter, MCP server,
scheduler, brokerage capability, or authoritative Product A run workflow.

## Reconciliation Principle

The implementation path established useful provider-neutral seams earlier than
the historical Product A blueprint anticipated. That sequence is accepted; it
does not change the product's authority boundaries.

Product A remains the primary product goal. The next work is not to expand the
generic runtime or connect a production model. It is to establish deterministic
Product A application and evidence contracts that can later support skills,
workflow manifests, MCP operations, and model-assisted synthesis without making
those outer layers the source of financial truth.

## Product A Remains the Primary Product Goal

Product A is still a personal, local-first U.S. premarket research brief with
immutable evidence, deterministic calculations, fail-closed behavior, and human
review. It is research software, not an execution system.

The approved Product A design remains the source for product scope, evidence
principles, research outputs, safety boundaries, and human-control requirements.
The large v0.1 implementation plan remains a master blueprint. Neither document
authorizes end-to-end implementation in one change.

Future Product A work should be sliced from demonstrated prerequisites. Likely
areas include:

- `ResearchPacket` and evidence-bundle boundaries;
- canonical Product A evidence and source metadata;
- immutable run context and run/revision identity;
- configuration and policy boundaries;
- deterministic packet assembly;
- publication and validation boundaries; and
- integration with the existing replay and regression capabilities.

These are responsibility areas, not speculative APIs. Each requires a separately
reviewed design before implementation.

## Role of AgentRuntime

AgentRuntime is a non-authoritative orchestration shell.

Its bounded generic interaction is:

```text
ModelPort invocation
        ↓
ModelResponse
        ↓
AssistantAction
├── FinalAnswer
└── ToolCall
       ↓
ToolRequest
       ↓
ToolRegistry
       ↓
ToolPort
       ↓
ToolResult
       ↓
ToolObservation
       ↓
next bounded model completion
```

`AgentRuntime` may own only generic model/tool mechanics:

- `ToolCall → ToolRequest` translation;
- exact tool lookup;
- sequential tool execution;
- immutable typed observation history;
- positive, bounded maximum-step semantics; and
- unchanged propagation of model, lookup, and tool failures.

It must not own Product A business semantics:

- market-calendar or run-window decisions;
- evidence freshness or source authority;
- financial calculations, scores, or sizing;
- eligibility gates or candidate ranking;
- publication validity;
- human approval state;
- watchlist business rules;
- run/revision identity;
- scheduled catch-up behavior; or
- Product A state transitions.

Product A domain/application code owns business semantics. A model may select
among capabilities exposed to it, but that selection cannot redefine policy,
authorize a consequential action, or override a deterministic result.

## Workflow Authority Hierarchy

The runtime authority hierarchy is, from most to least authoritative:

1. deterministic Product A domain/application contracts own business semantics;
2. declarative workflow manifests describe approved orchestration and typed
   artifact handoffs;
3. skills guide the host/model in using approved operations;
4. MCP exposes approved typed operations across a process boundary; and
5. `AgentRuntime` may select among approved capabilities without redefining the
   Product A workflow or financial policy.

A declarative workflow manifest is a contract describing approved orchestration;
it is not a general-purpose workflow engine. No generalized workflow engine is
justified by the current repository.

The following statements are independent and must never be collapsed:

```text
Tool registered
    !=
Tool authorized
    !=
Workflow permits execution
```

Registry membership proves only that exact lookup can find a capability. Future
consequential operations require explicit workflow permission and authorization
outside the registry and generic runtime.

## Lessons Adopted from `tradermonty/claude-trading-skills`

The public
[`tradermonty/claude-trading-skills`](https://github.com/tradermonty/claude-trading-skills)
repository is a selective design reference, not an implementation template.
Useful patterns to adopt when Product A prerequisites exist are:

- small composable skills with narrow purposes;
- declarative workflows that name ordered steps and artifact handoffs;
- explicit input and output artifacts;
- decision gates and visible manual-review gates;
- canonical metadata with a documented source-of-truth hierarchy;
- clear operator-facing workflows; and
- journaling, postmortem, and learning loops after the core workflow is stable.

The reference manifests are especially useful as examples of readable contracts
for ordered skills, artifacts, decision questions, and manual review. Product A
should adapt those ideas to typed, deterministic services rather than treating
manifest prose or model interpretation as business authority.

## Deliberate Differences from the Reference Repository

This repository retains stronger requirements already demonstrated by its code:

- provider-neutral typed boundaries;
- deterministic numeric truth;
- point-in-time evidence and explicit provenance;
- immutable replay and benchmark identity;
- walk-forward temporal separation and look-ahead leakage protection;
- deterministic regression gates; and
- fail-closed financial behavior.

No `skills-index.yaml` is added now. One production skill does not justify a
second canonical registry. Reconsider a registry when approximately 3–5
production skills or multiple declarative workflows exist and concrete drift or
discovery problems appear.

The repository will not adopt the reference project's package-distribution,
bilingual-documentation, generalized plugin, broad provider, portfolio, or
trading architecture without a demonstrated Product A requirement and a separate
approved design.

## Forward Roadmap

Exact future release numbers are provisional. Sequence and authority are the
important decisions.

### Completed foundation

1. v0.1 Deterministic Research Core — complete.
2. v0.2 Data Layer — complete.
3. v0.3 Deterministic Workflow — complete.
4. v0.4 Evaluation / Replay — complete.
5. v0.5 Minimal Agent Runtime Foundation — complete through Slice 3B.

### Forward direction

1. **Product A deterministic application/evidence contracts.** Stabilize typed
   evidence, run identity, configuration, packet assembly, publication, and
   replay integration in small reviewed slices.
2. **Product A skills and declarative workflow.** Add the minimum skills and one
   approved orchestration manifest only after their typed operations and
   artifacts exist.
3. **MCP boundary.** Expose the approved typed application operations without
   transferring policy or authorization into transport code.
4. **Production model provider adapter.** Translate provider-native requests and
   responses only after the Product A typed application/workflow boundaries are
   sufficiently stable.
5. **Shadow-mode automation, evaluation, and learning loop.** Exercise the full
   research workflow without execution, retain human gates, and use evidence,
   replay, regression, journaling, and postmortem results to guide separately
   approved changes.

Production model integration is deferred until Product A typed
application/workflow boundaries are sufficiently stable.

## Deferred Work

The following remain deferred:

- concrete `ResearchPacket`, run/revision, packet-assembly, and publication APIs;
- Product A domain-tool adapters;
- additional production skills and workflow manifests;
- `skills-index.yaml` or another skill registry;
- MCP server and operation schemas;
- production model SDKs and adapters;
- scheduling, catch-up, and autonomous operation;
- journaling/postmortem automation;
- SEC, macro, portfolio-risk, and second-provider capabilities;
- brokerage accounts, positions, orders, routing, cancellation, or execution;
- generalized workflow, plugin, provider, or permission frameworks.

Deferral is not approval. Each item requires the appropriate design and human
review before implementation.

## Curriculum / Future-Build Alignment

The v0.1→v0.5 sequence remains useful as an agent-engineering curriculum:
deterministic core, provider boundary, application orchestration, evaluation,
then generic model/tool contracts. It teaches the generic seams before a
production model exists and gives later Product A work testable dependency
boundaries.

Product development now returns to Product A. Future curriculum exercises should
be selected from the forward product prerequisites rather than expanding the
runtime for its own sake. A lesson may demonstrate a generic concept only when
its implementation also preserves the Product A authority hierarchy or remains
clearly isolated as infrastructure.

## Documentation Source-of-Truth Policy

Documentation has distinct roles rather than one merged master file:

1. approved Product A specifications and architecture deltas own architectural
   intent, product scope, and safety decisions;
2. deterministic typed domain/application code owns implemented business truth;
3. when created, declarative workflow manifests own approved orchestration and
   artifact handoffs, but not financial policy;
4. skills own discovery, loading, and usage guidance for approved operations;
5. MCP schemas derive from approved typed application operations;
6. immutable run artifacts own the frozen truth of individual Product A runs;
7. README and AGENTS are forward-facing derived guidance and must link to the
   applicable approved design; and
8. dated plans remain implementation and decision records, not automatically
   current roadmap authority.

When documents conflict, preserve historical records and correct the active,
lower-authority forward-facing document. Do not compact dated specifications or
plans into one mutable file. A small index may be added later if navigation
becomes difficult, without changing the records themselves.

## Acceptance Criteria

This reconciliation is complete when:

- historical plans remain unchanged;
- README describes the verified v0.1–v0.5 foundation and reconciled forward
  sequence;
- README and AGENTS explicitly describe `AgentRuntime` as non-authoritative for
  Product A;
- AGENTS records the Product A workflow authority hierarchy and prohibitions;
- Product A deterministic application/evidence work is the next priority;
- skills/workflows precede MCP, and MCP precedes production model integration;
- the one current skill and manifest inventory are inspected without inventing
  new manifests or registries;
- no Product A or runtime production behavior changes; and
- standard repository and documentation-diff checks pass.

## Non-Goals

This design does not:

- implement Product A application or evidence contracts;
- redesign the approved Product A specification;
- change financial formulas, scoring, sizing, gates, or classifications;
- change `AgentRuntime`, model, registry, or tool behavior;
- add skills, workflow manifests, MCP, providers, SDKs, or scheduling;
- add a workflow engine, skill registry, or plugin framework;
- add SEC, macro, portfolio, brokerage, or trading functionality;
- assign permanent semantic-version numbers to every future phase; or
- merge or rewrite historical specifications and plans.
