# Finance Research Agent

A local-first project evolving from deterministic market research into a trading
agent with a local simulated portfolio. The approved target combines six research
and decision roles in ChatGPT Work with local evidence validation, risk controls,
simulated execution, accounting, replay and evaluation.

The current build is **0.5.0.dev0**. It implements the research foundation:
market-data normalization, regime/setup analysis, frozen evidence packets,
validated reports, immutable storage/replay, evaluation and a typed local MCP
interface. Work integration, the six-role decision loop, simulated accounts and
a dashboard are approved design work, not available features yet. There is no
real-money trading or brokerage integration.

Start with the [quickstart](docs/quickstart.md) to install and check the existing
build. See [tasks](docs/tasks.md) for implemented foundations, current work and
completion evidence. The next stage is a bounded Work/data feasibility check,
followed by a reviewed end-to-end simulated-trading slice.

## Documentation

Each category has one current authoritative file. Update it in place; retired
specifications and plans are available through Git history.

| Read for | Reference |
| --- | --- |
| Governance and document ownership | [Constitution](docs/constitution.md) |
| Product scope, requirements and acceptance | [Specification](docs/spec.md) |
| Architecture and migration sequence | [Plan](docs/plan.md) |
| Work status and completion evidence | [Tasks](docs/tasks.md) |
| Reference findings and unresolved technical decisions | [Research](docs/research.md) |
| Entities and lifecycle meaning | [Data model](docs/data-model.md) |
| Interfaces, formulas and existing compatibility | [Contracts](docs/contracts.md) |
| Setup and first local checks | [Quickstart](docs/quickstart.md) |
| Requirement quality and implementation readiness | [Checklist](docs/checklist.md) |
| Recovery, diagnostics and release evidence | [Operations](docs/operations.md) |

Engineering instructions are in [AGENTS.md](AGENTS.md). Existing host skills are
indexed in [Project Skills](skills/README.md). Schemas, prompts, configuration
examples and workflow resources remain executable contracts; their responsibilities
are described in the canonical documentation.
