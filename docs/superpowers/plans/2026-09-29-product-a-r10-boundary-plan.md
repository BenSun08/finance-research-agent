# Product A R10 Typed Boundary Implementation Plan

> For each task, keep the RED → GREEN evidence, run the named checks, review the
> diff, and commit that task. Each feature slice ends with independent review,
> coverage analysis, push, PR, CI/review checks, and merge.

**Goal:** Expose the completed deterministic Product A pipeline through the
approved narrow typed application, stdio MCP, CLI, plugin, skill, and workflow
boundaries.

**Architecture:** Follow the staged R10a → R10b → R10c order in the design.
Domain/application services remain authoritative. Transport validates and
dispatches; CLI remains diagnostic/replay-only; skills and one manifest guide
manual orchestration without running workflows.

**Tech stack:** Python 3.12+, existing Pydantic contracts, existing Product A
services and ports, existing local package tooling, stdio MCP library only if
approved by the R10b dependency review, pytest, Ruff, mypy.

**Design:** `docs/superpowers/specs/2026-09-29-product-a-tool-and-orchestration-boundary-design.md`

## Global Constraints

- Use exactly the eleven operation names in the design and Product A spec §9.
- Keep the current Alpaca market-data and local market-calendar source scope.
- Keep runtime dependencies empty until an approved R10b transport dependency
  is required; do not add a framework for convenience.
- No arbitrary URL, path, shell, eval, risk override, account, order, broker, or
  trading operation.
- No network transport, production model, active schedule, workflow engine,
  skill registry, generic permission boolean, or financial policy in prompts.
- Preserve existing APIs and generic AgentRuntime behavior.
- All CI and tests remain offline and credential-free.

## Review Focus

- Unknown fields, overlong values, duplicate identifiers, and malformed nested
  payloads must fail before reaching services.
- A malicious or unknown operation name must never resolve to an arbitrary
  method or registered-but-unauthorized side effect.
- Provider/config/internal exceptions must not disclose credentials, paths,
  exception text, response bodies, or stack traces.
- Repeated mutations and interrupted runs must preserve the services'
  idempotency, optimistic concurrency, and checkpoint behavior.
- Missing, reordered, symlinked, or changed skill resources must fail package
  verification or change the content digest deterministically.

---

## Slice R10a: Application Facade and Operation Contracts

### Task 1: Define the transport-neutral eleven-operation contract

**Tests first:** Add contract tests for the exact ordered operation allowlist,
strict request/result schemas, unknown field rejection, closed enums, bounds,
and prohibition of caller-controlled provider/URL/path/deadline/risk parameters.
Run the focused contract tests and record RED before implementation.

**Implementation:** Add request/result models and operation metadata only; do
not add MCP imports. Build a total mapping from each operation name to one typed
application entry point. Reject unlisted names before dispatch.

**Verify and commit:** Run focused tests, Ruff and mypy on changed modules,
schema-export/drift checks, diff review; commit as `feat: define Product A operation contracts`.

### Task 2: Add a trusted `ApplicationServices` composition root

**Tests first:** Test construction from injected repositories/providers/clock,
default dependency ownership, unavailable/invalid configuration behavior,
and ensuring no provider or filesystem access occurs before the corresponding
service call. Add feedback-service tests first: reject unknown/unpublished runs,
bound each score to 1–5 and notes to 1,000 characters, accept only zero to five
citation reviews selected for that exact published bundle, append distinct
immutable records, preserve the bundle hash, and list by `recorded_at` then
`feedback_id`.

**Implementation:** Add the bounded append-only feedback service and storage
port needed by `record_run_feedback`; leave scorecard aggregation to R11. Add
`application/services.py` as an explicit dependency container/factory. Reuse
the existing `RunService`, preparation, publication, replay, configuration,
and watchlist APIs. Do not copy their policy.

**Verify and commit:** Run focused application/composition tests, full pytest,
Ruff, mypy, diff review; commit as `feat: compose Product A application services`.

## Slice R10b: stdio MCP and Diagnostic/Replay CLI

### Task 3: Add the stdio-only MCP server

**Tests first:** Test initialize/tools-list exactness, request/response schema
validation, one-to-one dispatch, errors, secret redaction, malformed operation
rejection, stdio-only startup, and absence of TCP/listener creation. Fakes must
prove transport does not call adapters or repositories directly.

**Implementation:** Register only the exact eleven handlers and call
`ApplicationServices`. Add the smallest pinned transport dependency only after
the adapter interface test establishes its need; test protocol behavior with
fakes and no network.

**Verify and commit:** Run protocol/security suites, full pytest, Ruff, mypy,
dependency lock/drift checks and coverage; commit as `feat: expose typed Product A stdio operations`.

### Task 4: Add diagnostic and frozen-replay CLI commands

**Tests first:** Verify help/argument contracts; status/config diagnostics;
frozen replay uses stored artifacts only; unknown commands, paths, run
overrides, prepare/publish/mutation attempts are rejected.

**Implementation:** Reuse `ApplicationServices`; do not invoke the MCP client,
duplicate business logic, or provide arbitrary filesystem path arguments.

**Verify and commit:** Run CLI, replay, and security tests, full checks, coverage,
diff review; commit as `feat: add diagnostic and replay CLI`.

## Slice R10c: Plugin, Skills, Workflow, and Provenance

### Sequencing correction selected on 2026-10-09

Selected under the user's standing delegated decision authority after PR #69
merged as `807f607`. Tasks 5 and 6 are delivered in one reviewed feature with
separate tested subtask commits. Task 5 needs exact installed premarket resources
for its digest, while Task 6 creates those resources; the original sequential
merge gate would require a placeholder or a competing source copy. Create and
verify the canonical contracts first, package those same root bytes, then wire
their installed digest and verify the finite protocol/replay matrix before the
feature PR. The typed operations and runnable transport remain prerequisites.

Keep the approved local compatibility manifest at `.codex-plugin/plugin.json`,
plugin identity `ai-market-research-agent`, and version `0.1.0`. Correct the older
blueprint's direct MCP map to the supported `.mcp.json` `mcpServers` wrapper;
its sole server runs `ai-market-research-mcp` with no arguments or secret values.
The compatibility layout and wrapper are documented in the
[official plugin packaging guide](https://developers.openai.com/plugins/build/plugins).
Do not install or enable the plugin or activate a schedule in this code slice.

Production provenance reads the fixed installed distribution resources and
fails closed when they are missing or invalid. Offline source tests inject a
trusted synthetic skill digest through the existing component-version callback.
No mutable source fallback or caller-supplied MCP digest is introduced. Wheel and
sdist verification uses temporary builds/installations and includes root/ancestor
symlink rejection, exact resource allowlists and no source-import fallback.

### Task 5: Add the local Product A plugin package and component digest

**Tests first:** Test manifest schema, exact resource allowlist, package build
contents, stable digest from canonical path/bytes framing, digest change on
content/path changes, path traversal/symlink rejection, and run/replay version
drift behavior.

**Implementation:** Package only canonical root skill resources and the
approved local plugin manifest. Compute the bundle digest from packaged bytes
and inject trusted `ComponentVersions` through the composition/run service.

**Verify and commit:** Run packaging, run identity, replay and contract tests,
full checks and coverage; commit as `feat: version Product A skill bundle`.

### Task 6: Add the three skills and the single premarket workflow contract

Start after Tasks 1–4's typed operation contracts/transport and the runnable
bootstrap have merged. Deliver alongside Task 5 under the sequencing correction
above; all contracts, packaged resources and provenance must be verified before
their combined feature is published.

**Tests first:** Test exact shared skill frontmatter/sections, operation
allowlist parity for every `mcp_tool` workflow step (the eleven MCP operations
only), resource existence and safe relative paths, no market-regime/watchlist
workflow manifests, ordered premarket steps including the single
`codex_synthesis` step `research_brief_draft`, typed artifact handoffs, gates,
two-repair bound, and terminal outcomes.

**Implementation:** Update `market-regime`; add `premarket-research` and
`watchlist-management`; add only the adjacent premarket manifest. Keep
`skills/README.md` link-only. No skill-local Python, provider, financial, or
policy scripts.

**Verify and commit:** Run skill/protocol/replay/documentation/package checks,
full pytest, Ruff, mypy, coverage and drift checks; commit as `feat: add Product A skills and workflow contract`.

### Task 7: Close R10 review and deliver in reviewed feature slices

For each code feature slice, record focused RED/GREEN, tests, full checks,
independent whole-slice review findings, line and branch coverage, and final diff
scope. Address findings with failing tests first. Push each reviewed slice, open
its PR, check exact-head CI/reviews/inline threads, merge only when green and
review-clean, then verify the merge SHA on `main`.

## Deferred Slice R11

Do not implement the 25-scenario scorecard, broad release gates, alias removal,
or release-readiness claims inside R10. R11 begins after R10 is merged and gets
its own design/readiness pass against current source scope.
