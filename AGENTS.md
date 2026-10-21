# Project Instructions

## Context

Start with [tasks](docs/tasks.md) for approved scope and implementation status.
Follow the [constitution](docs/constitution.md) for governance and document ownership.
Approved target architecture does not imply implemented functionality.

Read the relevant references before changing:

- Product behavior: [spec](docs/spec.md).
- Architecture and prerequisites: [plan](docs/plan.md), [research gates](docs/research.md#implementation-gates).
- Types, calculations, interfaces, storage or replay: [data model](docs/data-model.md), [contracts](docs/contracts.md).
- Setup and operation: [quickstart](docs/quickstart.md), [operations](docs/operations.md).
- Implementation readiness: [checklist](docs/checklist.md).

## Workflow

- Inspect relevant code, contracts and tests; classify changes as bounded or architectural.
- Present scope, affected files and verification before implementation; obtain approval
  unless already authorized. Architectural changes also need alternatives and trade-offs.
  Obtain approval before expanding scope.
- Use the tool-agnostic repository workflow. Invoke formal planning only when requested
  or needed for an approved architectural change, not automatically for code edits.
- Update canonical documents in place; keep status/evidence in tasks and history in Git.
- Review the final diff before committing. Report local checks, remote CI, operator
  evidence and publication separately.

## Engineering and checks

- Use Python 3.12+; write source, tests, configuration, logs, reports and docs in English.
- Keep dependencies within approved scope and versions aligned across `pyproject.toml`,
  package `__version__` and current-version documentation.
- Test observable behavior changes. Use offline fakes/network guards; live checks are opt-in.
- Use the development environment, normally `.venv/bin`, and run before review:

```text
pytest
ruff check .
mypy src
```

For documentation, schema or interface changes, also run:

```text
python -m scripts.check_docs_examples
python -m finance_research_agent.schema_export --check
git diff --check
```
