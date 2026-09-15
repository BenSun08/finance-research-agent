# Project Instructions

## Purpose and Product Scope

`finance-research-agent` is a local-first, AI-assisted market-research and
decision-support project. Product A is a personal premarket research brief for
U.S.-listed common stocks and non-leveraged, non-inverse ETFs. It is research
software, not a brokerage or trading system.

Completed foundation: v0.5 Agent Runtime through Slice 3B, including approved
provider-neutral model/tool contracts, assistant actions, synchronous ports,
deterministic tool registry, fake adapters, and the minimal bounded runtime.

Current forward direction: Product A deterministic application/evidence
contracts under the approved
`docs/superpowers/specs/2026-09-15-roadmap-reconciliation-design.md`.
Production model integration and runtime capabilities beyond Slice 3B require
separately approved designs and are not the immediate next milestone.

Allowed:
- provider-neutral model message/request/response contracts and model port
- deterministic fake model for offline dependency substitution and testing
- provider-neutral FinalAnswer / ToolCall assistant action contracts
- bounded synchronous AgentRuntime over injected ModelPort and ToolRegistry
- runtime-owned ToolCall to ToolRequest translation and sequential tool execution
- immutable ToolObservation history pairing each successful result with its call
- provider-neutral tool definition/request/result contracts and tool port
- immutable caller-ordered tool registry for capability discovery and exact lookup
- deterministic fake tool for offline dependency substitution and testing
- market-data-only Alpaca historical client integration
- authenticated historical market-data reads
- request mapping
- provider response materialization/pagination
- provider-level error handling
- offline fake/mock tests
- mapping into existing normalization boundary
- deterministic application/workflow orchestration over the existing
  historical-data and regime capabilities
- evaluation work over existing deterministic/application capabilities:
  - offline evaluation fixtures
  - deterministic scenario/replay evaluation
  - benchmark/evaluation harnesses
  - evaluation metrics and regression gates

Still prohibited:
- Alpaca brokerage/trading capabilities
- accounts, positions, and buying power
- orders, routing, cancellation, and execution
- streaming
- MCP
- scheduling
- unbounded or autonomous runtime capabilities beyond the approved Slice 3B loop
- SEC/macro
- portfolio risk
- second providers
- production model/LLM integration before Product A typed application/workflow
  prerequisites are stable and separately approved
- direct exposure of domain functions as model tools

## Safety, Numeric Truth, and Approval

- Never implement automatic trade execution. Do not add account, holding,
  position, buying-power, order, routing, cancellation, or execution
  capabilities without a separately approved future design.
- Human review and approval remain mandatory decision gates. No generated plan,
  report, state, or message constitutes approval to trade.
- Deterministic code owns numeric truth: normalized values, calculations,
  metrics, scores, levels, sizing, gates, and state transitions. Language-model
  output may explain deterministic results but must not invent, recalculate, or
  alter them.
- Keep credentials, private configuration, local data, runtime artifacts, and
  generated reports outside tracked source.
- Slice 2 defines tool execution contracts only. ToolRegistry provides capability
  discovery and lookup, not planning or authorization. Before enabling
  consequential actions, future runtime work must distinguish read-only/query
  tools from side-effecting command tools and define their authorization gates.
  No permission framework or safety boolean is defined by these contracts.
- Slice 3A separates ToolCall model intent from ToolRequest runtime execution
  intent. Both validate data shape; neither establishes authorization. Slice 3B
  owns translation, exact registry lookup, sequential execution, and paired
  ToolObservation history. Registry membership is not authorization for a
  consequential action; authorization, policy, and tracing remain deferred.
- Slice 3B counts one model completion as one step. A positive max_steps is
  mandatory. FinalAnswer stops immediately; a ToolCall on the final permitted
  step executes before explicit exhaustion. Lookup/model/tool failures propagate
  unchanged. No retry, approval framework, production provider, domain-tool
  adapter, or additional financial capability is introduced.
- Existing deterministic financial domain logic stays outside the agent package.
  Future domain-tool adapters may depend on tool contracts and domain/workflow
  APIs; the domain and workflows must not depend on agent contracts or runtime.

## Product A Workflow Authority

Product A remains the primary product goal. Its authority hierarchy is:

```text
deterministic domain/application contracts
        ↓
declarative workflow contracts
        ↓
skills
        ↓
MCP typed operations
        ↓
optional AgentRuntime orchestration
```

- Product A domain/application code owns business semantics, including evidence
  freshness and source authority, financial calculations, scores, sizing,
  eligibility gates, candidate ranking, publication validity, human approval
  state, watchlist rules, run/revision identity, scheduling semantics, and
  Product A state transitions.
- A declarative workflow manifest is a contract describing approved
  orchestration and artifact handoffs; it is not a general-purpose workflow
  engine.
- Skills guide use of approved operations. MCP may later expose approved typed
  operations. Neither layer becomes the source of deterministic financial truth.
- `AgentRuntime` is a non-authoritative generic model/tool orchestration shell.
  Do not use it as Product A's authoritative workflow engine.
- Registry membership establishes capability discovery only:
  `Tool registered != Tool authorized != Workflow permits execution`.
- Do not move deterministic financial policy into prompts or model decisions.
- Do not let model reasoning define evidence freshness or source authority.
- Do not let a model alter deterministic scores, sizing, gates, or state
  transitions.
- Do not equate `ToolRegistry` membership with authorization.
- Do not create production provider integration before Product A typed
  application/workflow prerequisites are stable and separately approved.
- Do not add a generalized workflow engine. Do not add `skills-index.yaml` now;
  reconsider a canonical skill registry when approximately 3–5 production skills
  or multiple declarative workflows create a demonstrated discovery or drift
  problem.

Future Product A architecture may be proposed in small reviewed slices around:

- `ResearchPacket` and evidence/source metadata contracts;
- immutable run context and run/revision identity;
- configuration and policy boundaries;
- deterministic packet assembly;
- publication and validation boundaries;
- Product A replay integration;
- Product A skills and declarative workflow contracts after typed prerequisites;
- MCP over approved application operations after workflow contracts stabilize;
  and
- production model provider integration only after those boundaries stabilize.

This list identifies future design areas; it does not authorize implementation.
MCP, production model integration, scheduling, and additional Product A
capabilities remain prohibited until separately designed and approved.

  ## Planning and Skill Policy

- This repository uses a tool-agnostic engineering workflow.
- Do not automatically invoke Superpowers, brainstorming, writing-plans,
  Plan Mode, or any other planning skill solely because a task involves
  code changes.

- Follow the repository's Change Process as the default workflow.

- For bounded changes and release-scoped work under an already approved
  architecture:
  1. inspect the relevant repository context;
  2. present a short design in chat;
  3. wait for explicit human approval;
  4. implement the approved scope;
  5. run relevant tests and quality checks;
  6. show the final diff for review.

- Do not create a new architectural specification or implementation-plan
  document for routine bounded work or for a small vertical slice that is
  already covered by an approved architecture.

- Superpowers or other formal planning workflows may be used only when:
  - the human explicitly requests them; or
  - the change introduces a genuinely new subsystem;
  - the change materially alters an approved architecture;
  - the change modifies important cross-component interfaces;
  - the change begins a new product or major product capability.

- If one of those architectural conditions is discovered during
  implementation, stop before broadening the scope and ask the human
  whether a formal architecture/design workflow should be used.

- Existing approved specifications and release scope are authoritative.
  Do not replace or duplicate them with newly generated planning documents
  unless explicitly requested.

## Change Process

- Classify each proposed change as bounded or architectural before
  implementation.

- For bounded changes:
  - inspect the relevant existing code and documentation;
  - present the intended change, affected files, and verification approach;
  - obtain explicit human approval;
  - implement only the approved scope;
  - add or update tests when observable behavior changes.

- For architectural changes:
  - inspect the existing architecture and relevant specifications;
  - present design alternatives and trade-offs;
  - obtain explicit human approval;
  - update or create a written specification when necessary;
  - create an implementation plan when the work spans multiple components.

- Never silently broaden the current product or release milestone.

- Before committing, run the relevant tests and quality checks and review
  the final diff for unrelated or unintended changes.

## Engineering Baseline

- Use Python 3.12 or newer.
- Keep source code, tests, configuration, logs, reports, and technical
  documentation in English.
- Keep runtime dependencies empty until an approved milestone requires them.
- Keep `project.version` in `pyproject.toml`, package `__version__`, and current
  version documentation aligned. The current development version is `0.5.0.dev0`.
- Run the repository checks before requesting review:

  ```text
  pytest
  ruff check .
  mypy src
  ```
