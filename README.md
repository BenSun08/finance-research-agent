# Finance Research Agent

An open-source AI-assisted market research and portfolio decision-support system.

## Goals

This project is designed to explore practical AI Agent Engineering concepts including:

- Agent Skills
- deterministic financial tools
- structured market data
- declarative workflows
- evaluations
- human-in-the-loop decision making
- agent runtimes
- MCP
- production automation

## Safety Boundary

This project is designed for research and decision support.

It does not automatically execute trades.

Human approval remains the final decision gate.

## Development Roadmap

- v0.1 Deterministic Research Core + First Market Regime Skill
- v0.2 Data Layer
- v0.3 Workflow
- v0.4 Evals
- v0.5 Agent Runtime
- v0.6 MCP
- v0.7 Automation / Production

## Status

v0.1.0 is released with a deterministic, synthetic-data-only market-regime core.
v0.2 includes an offline-tested, market-data-only Alpaca historical daily-bars
client that maps fully materialized SDK responses into the provider-independent
normalization boundary. Default tests require no credentials or network access;
trading remains unavailable.

v0.3 adds provider-independent application orchestration around historical-data
and deterministic market-regime capabilities. The application depends on the
provider-neutral `HistoricalBarsFetcher` port, supplied by the Alpaca adapter;
request-global failures remain provider-neutral. The workflow remains
offline-testable, and trading remains unavailable.
