# Project Instructions

## Start here

This repository is being redesigned into a local-first trading decision agent
with simulated portfolio execution. The current build still implements the
research profile. Read [tasks](docs/tasks.md) at the start of work to identify
what is implemented, approved, pending or blocked. Do not infer implementation
status from the target architecture.

- Read [constitution](docs/constitution.md) before changing authority, scope,
  policy or document ownership.
- Read [spec](docs/spec.md) for product behavior and acceptance requirements.
- Read [plan](docs/plan.md) and the relevant [research gates](docs/research.md#implementation-gates)
  before architectural work or starting the next delivery stage.
- Read [data-model](docs/data-model.md) and [contracts](docs/contracts.md) before
  changing types, calculations, APIs, storage, runtime boundaries or replay.
- Read [quickstart](docs/quickstart.md) for supported commands and
  [operations](docs/operations.md) for recovery, release and operator evidence.
- Use [checklist](docs/checklist.md) to assess requirement readiness; record work
  status and evidence only in tasks.

## Change process

Classify a change as bounded or architectural. Inspect the checkout, current
contracts and relevant tests before proposing it. For bounded work, present a
short design with affected files and verification, obtain approval, implement
only that scope, run checks and show the final diff. Existing explicit approval
covers its stated scope; do not repeatedly request the same approval.

For architecture or cross-component interface changes, present alternatives and
trade-offs, obtain approval, and update the canonical spec/plan/contracts as
needed. If implementation reveals a material scope expansion, stop that expansion
and resolve it before proceeding. Never silently broaden a release milestone.

Use the tool-agnostic repository workflow. Do not automatically invoke Superpowers,
brainstorming, writing-plans or Plan Mode solely because code changes are involved.
Use formal planning only when requested or needed for an approved architectural
change. Do not create dated specs/plans, per-slice planning copies or extra task
reports. Update the document that owns the information and link to it elsewhere.

## Engineering baseline

- Python 3.12 or newer. Source, tests, configuration, logs, reports and technical
  documentation are English.
- Keep `pyproject.toml`, package `__version__` and current-version documentation
  aligned. The current development version is `0.5.0.dev0`.
- Preserve the approved runtime dependency set; adding dependencies requires a
  concrete justified implementation scope.
- Add or update meaningful tests when observable behavior changes. Use offline
  fakes and the repository network guard by default; live checks are opt-in.
- Review the final diff for unintended changes before committing. Report local
  checks, remote CI, operator evidence and publication status separately.

Run the repository checks before requesting review:

```text
pytest
ruff check .
mypy src
```

For documentation/schema/interface changes, also run:

```text
python -m scripts.check_docs_examples
python -m finance_research_agent.schema_export --check
git diff --check
```

Use the installed development environment, normally `.venv/bin`. Release
procedures and additional offline/packaging checks are in operations. Never
claim a live, installed-host or simulated-trading result from a documentation
change or synthetic test alone.
