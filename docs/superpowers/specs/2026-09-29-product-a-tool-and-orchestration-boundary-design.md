# Product A Tool and Orchestration Boundary Design

Date: 2026-09-29
Status: Selected design under the user's standing delegated decision authority

## Purpose

Define the next migration slices after the deterministic Product A run,
publication, replay, and telemetry foundations. The slices add the narrow user
and orchestration boundary from the approved Product A specification without
moving financial policy into transport code, skills, workflow prose, or the
generic AgentRuntime.

## Current Verified Starting Point

- Product A run initialization, configuration snapshots, market-data
  collection, quality evaluation, packet construction and validation,
  publication, replay, and run-scoped telemetry exist on `main` after PR #60.
- `ComponentVersions` and `application/run_service.py` exist.
- There is no trusted `ApplicationServices` composition root, MCP server,
  diagnostic/replay CLI, Product A plugin package, or contract checker.
- A version-checked watchlist service exists; there is no run-feedback service
  or append-only feedback store.
- The 2026-08-31 skill/workflow delta requires typed application operations and
  the plugin package before implementing its skill bundle and workflow tasks.
- The Product A source scope remains the currently implemented Alpaca
  market-data adapter and local market-calendar adapter.

## Decision

Use staged, typed boundaries rather than one broad implementation or a
skills-first implementation:

1. **R10a — Application facade and operation contracts.** Add one trusted
   composition root over injected Product A services and define the exact
   eleven-operation request/result allowlist from Product A design §9. Keep
   operation schemas transport-neutral. No handler may call adapters or
   repositories directly.
2. **R10b — Stdio MCP and diagnostic/replay CLI.** Expose only the exact typed
   operation allowlist over stdio. Add a CLI limited to diagnostics and frozen
   artifact replay; it does not prepare, publish, or mutate watchlists.
3. **R10c — Plugin, skills, workflow, and provenance.** Add the approved local
   plugin package, the three bounded skills, one adjacent premarket workflow
   manifest, exact skill-bundle hashing, run provenance integration, and
   documentation/contract checks. Start only after R10a/b operation contracts
   and the plugin package are present.
4. **R11 — Evaluation and release gates.** Keep the 25-scenario shadow
   scorecard, offline/security/replay/documentation gates, compatibility audit,
   and release documentation as a later separately reviewed slice.

Each code feature slice has test-first work, a separate commit, whole-slice
review and coverage analysis, push, PR, CI/review check, and merge. The skill
and workflow task starts only after the transport-neutral operation contracts
and local plugin package are present.

## Exact Operation Boundary

The transport exposes these eleven operations, with names and business behavior
remaining owned by the Product A specification and typed application services:

1. `get_system_status`
2. `validate_configuration`
3. `prepare_premarket_run`
4. `get_run_status`
5. `get_report`
6. `validate_and_publish_brief`
7. `publish_reduced_report`
8. `list_watchlist`
9. `upsert_watchlist_item`
10. `remove_watchlist_item`
11. `record_run_feedback`

The premarket workflow also has one bounded `codex_synthesis` step named
`research_brief_draft`, as specified by the 2026-08-31 skill/workflow delta.
That name identifies a host synthesis action, not an MCP operation and not a
capability registered in `ApplicationServices`. Only workflow steps with
`kind: mcp_tool` may reference the eleven-operation MCP allowlist.

Transport-neutral request/result schemas reject unknown fields and enforce
bounded strings, collections, enums, and identifiers. They do not accept a
provider choice, arbitrary URL, filesystem path, deadline, risk policy override,
or generic command. Existing deterministic application APIs own run-state
checks, watchlist concurrency, validation, publication, and feedback persistence.

## Components and Authority

- `application/services.py` is the trusted environment composition root. It
  constructs/injects application dependencies once, owns no business policy,
  and exposes only typed service entry points. Tests can supply fakes without
  process-global state.
- `mcp_server` validates transport input, invokes one allowlisted service
  operation, and serializes its typed result. It uses stdio only and opens no
  listening socket.
- The CLI reuses the composition root and offers diagnostics plus zero-network
  frozen replay only. It does not duplicate application decisions.
- Skills guide host behavior over the typed operations. The premarket manifest
  describes order, artifacts, repair bounds, and manual gates; it is not an
  interpreter, scheduler, permission engine, or source of business rules.
- MCP registration, skill instructions, and AgentRuntime registry membership
  are capability discovery only. They do not grant authorization. Consequential
  state transitions remain enforced by deterministic Product A services.

## Mutation and Failure Semantics

- Read-only status, configuration validation, status lookup, report lookup,
  and watchlist listing remain side-effect free.
- `prepare_premarket_run` may create/resume only the spec-authorized run and
  checkpoints. Publication operations accept only the typed frozen-run inputs
  already required by application services.
- Watchlist writes use the existing optimistic-concurrency service and take
  effect for a later run/revision. Feedback accepts a published run ID, three
  rubric scores from 1 through 5, optional English notes of at most 1,000
  characters, and zero to five deterministic citation-entailment reviews.
  Feedback appends an immutable record and does not modify a published bundle,
  market state, account state, or risk policy. R11 may derive a scorecard from
  these records.
- No operation modifies risk policy or grants trading approval.
- Known domain failures map to the existing closed error vocabulary. Unknown
  internal exceptions return a generic non-sensitive failure; transport output
  never includes credentials, private paths, raw provider messages, stack
  traces, response bodies, or arbitrary input echo.
- Stdio disconnects do not cancel durable run state or trigger background work;
  existing checkpoints support an explicit later resume.

## Dependencies and Packaging

- Keep runtime dependencies unchanged unless the selected MCP transport package
  is explicitly required by the approved R10b design. If needed, add the
  smallest pinned dependency in its own reviewed change and keep CI offline.
- Do not distribute a `.skill` package or create a general plugin framework.
  Package only the approved local plugin manifest and canonical root skill
  resources.
- `skill_version` is a deterministic hash of exact installed skill bundle
  paths and bytes. It is distinct from prompt, report-template, MCP, plugin,
  schema, and core versions, and is frozen into run provenance before execution.
- Keep the recurring schedule definition paused. Do not activate scheduling.

## Security and Scope Constraints

- Preserve the currently configured Alpaca market-data and local
  market-calendar roles. Do not add SEC, macro, company-IR, news, a second
  provider, production model integration, MCP-over-network, scheduling, or
  brokerage/trading operations.
- Do not expose arbitrary shell, Python evaluation, file paths, URLs, package
  installation, accounts, positions, buying power, orders, routing,
  cancellation, or execution.
- No generalized workflow engine, skill registry, plugin framework, generic
  authorization boolean, or Product A delegation to `AgentRuntime`.
- Keep human review/approval gates and deterministic numeric truth unchanged.

## Verification Strategy

Every code subtask follows RED → GREEN → focused tests → relevant repository
checks → review of the final diff → commit. Each feature receives an independent
whole-feature review and coverage analysis before push/PR. The tests must prove:

- exact eleven-operation registration and rejection of unlisted/unknown input;
- one-to-one handler-to-application dispatch with no transport-owned policy;
- stdio operation with no socket listener and redacted failures;
- CLI diagnostic/replay-only behavior and zero provider access during replay;
- optimistic concurrency and run-state checks remain in existing services;
- skill resource/path safety, workflow ordering and artifact references,
  package resource inclusion, deterministic skill digest, and replay version
  drift; and
- offline CI and migration compatibility with existing generic AgentRuntime.

## Alternatives Considered

- **One all-at-once R10 PR:** shorter sequencing but couples missing composition,
  transport, CLI, packaging, and skill contracts, making failures and review
  findings difficult to isolate.
- **Skills/workflow first:** rejected because the approved skill delta gates
  implementation on typed MCP operations and the plugin package; prose would
  otherwise point at contracts that do not exist.
- **Selected staged boundary:** makes each prerequisite explicit and reviewable
  while preserving the approved end state and source scope.

## Non-Goals

This design does not activate a schedule, add new evidence sources, implement a
production model/provider, change financial calculations, make MCP the source of
policy, create a generic workflow engine, or claim shadow-mode release readiness.
