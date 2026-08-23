# Project Instructions

## Purpose and Product Scope

`finance-research-agent` is a local-first, AI-assisted market-research and
decision-support project. Product A is a personal premarket research brief for
U.S.-listed common stocks and non-leveraged, non-inverse ETFs. It is research
software, not a brokerage or trading system.

The current milestone is **v0.0.1 Repository Foundation only**. Do not implement
market-research functionality during this milestone. In particular, do not add
MCP integration, Alpaca integration, scheduling, portfolio-risk logic, or
market-regime logic yet.

The existing Product A v0.1 implementation plan is an acceptance-traceable
master blueprint. It is not authorization to execute the plan end-to-end or to
pull later-slice functionality into the current milestone.

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

## Change Process

- Classify each proposed change as bounded or architectural before implementation.

- For architectural changes:
  - inspect the existing repository and relevant design documents first;
  - present the proposed design, important alternatives, and trade-offs;
  - obtain explicit human approval before implementation;
  - record the approved design in a specification when the change introduces
    a new subsystem, changes important interfaces, or materially changes the
    architecture;
  - create an implementation plan before making code changes when the work
    spans multiple components or requires multiple implementation steps.

- For bounded changes:
  - briefly describe the intended change, affected files, and verification
    approach;
  - obtain explicit human approval before implementation;
  - add or update tests when observable behavior changes.

- Keep every change within the currently approved product and release scope.
  If implementation reveals that the change requires broader architectural
  work, new interfaces, or additional subsystems, stop and request approval
  before expanding the scope.

- Before committing, run the relevant tests and quality checks and review the
  final diff for unrelated or unintended changes.

- Planning or brainstorming tools may be used when helpful, but no specific
  tool or plugin is required. The repository's approved specifications,
  active milestone, and these engineering rules are authoritative.

## Engineering Baseline

- Use Python 3.12 or newer.
- Keep source code, tests, configuration, logs, reports, and technical
  documentation in English.
- Keep runtime dependencies empty until an approved milestone requires them.
- Run the repository checks before requesting review:

  ```text
  pytest
  ruff check .
  mypy src
  ```
