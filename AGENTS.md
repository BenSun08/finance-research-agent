# Project Instructions

## Purpose and Product Scope

`finance-research-agent` is a local-first, AI-assisted market-research and
decision-support project. Product A is a personal premarket research brief for
U.S.-listed common stocks and non-leveraged, non-inverse ETFs. It is research
software, not a brokerage or trading system.

Current milestone: v0.2 Data Layer

Allowed:
- market-data-only Alpaca historical client integration
- authenticated historical market-data reads
- request mapping
- provider response materialization/pagination
- provider-level error handling
- offline fake/mock tests
- mapping into existing normalization boundary

Still prohibited:
- accounts
- positions
- buying power
- orders
- trading
- streaming
- MCP
- scheduling
- SEC/macro
- portfolio risk
- LLM integration

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
- Run the repository checks before requesting review:

  ```text
  pytest
  ruff check .
  mypy src
  ```
