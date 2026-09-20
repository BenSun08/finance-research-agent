# R3 Task 5 Implementation Report

## Status

Complete as a bounded local implementation on the existing
`refactor/spec-plan-conformance` worktree. The implementation commit is
`29ea648` (`feat: add immutable run storage primitives`), based on the
required BASE `f93e184`. No push, merge, pull request, subagent dispatch, R4+
work, or use of the 2026-09-15 roadmap reconciliation occurred.

## Scope and files

The implementation remains limited to additive run-storage contracts and the
filesystem adapter:

- `src/finance_research_agent/adapters/filesystem.py`
  - Added `FileSystemRunRepository` with revision allocation, staging-only
    creation, leases, atomic heartbeat replacement, checkpoint persistence,
    immutable evidence cutoff, canonical bundle/report hashing, atomic final
    directory publication, index/latest updates, and read-only published
    lookups.
  - Added typed `PathNotAllowedError`, `LeaseHeldError`, and
    `PublicationError` boundaries.
- `src/finance_research_agent/domain/models.py`
  - Added immutable `RunLease`, `RunCheckpoint`, `StoredRun`,
    `PublishedRunBundle`, and `PublishedArtifact` values while retaining the
    existing strict `RunContext` and `RunKey` contracts.
- `src/finance_research_agent/application/ports.py`
  - Added the provider-neutral `RunRepository` protocol additively.
- `src/finance_research_agent/application/__init__.py`
  - Exported `RunRepository` through the existing application boundary.
- `tests/unit/test_filesystem_store.py`
  - Added lease expiry/heartbeat, checkpoint/cutoff immutability, publication
    failure, hash, visibility, publication-order, path-safety, symlink, layout,
    and fixture-contract coverage.
- `tests/integration/test_run_identity.py`
  - Added automatic r1 idempotency and manual r2 allocation coverage.
- `tests/fixtures/artifacts/minimal-frozen-run.json`
  - Added the strict minimal bundle fixture used by the storage tests.

No dependency was added. The existing Python, Pydantic, and standard-library
dependencies are sufficient.

## TDD evidence

The initial storage tests were written before the production storage module
existed. The first focused run was intentionally RED:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py
2 errors during collection
ModuleNotFoundError: No module named 'finance_research_agent.adapters.filesystem'
```

After the smallest storage contracts and adapter were added, the focused suite
reached GREEN at `14 passed`, then `15 passed` after the cutoff persistence
correction, and finally `16 passed` after the review-driven latest-pointer
case was added.

The second RED/GREEN cycle covered publication ordering. The regression test
failed under the original “last publication wins” implementation:

```text
1 failed, 1 passed, 13 deselected
AssertionError: ...-r1 == ...-r2
```

The minimal GREEN fix makes `latest` point to the highest published revision,
so publishing r2 before r1 cannot regress the pointer. The final focused run
was:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py --disable-warnings
16 passed in 0.48s
```

## Verification

Final post-commit checks on implementation commit `29ea648`:

- `../../.venv/bin/pytest -q --disable-warnings --tb=short` — `1187 passed,
  1 warning in 16.30s`.
- `../../.venv/bin/ruff check .` — `All checks passed!`.
- `../../.venv/bin/mypy src` — `Success: no issues found in 44 source
  files`.
- `git diff --check` — passed with no output.

The one pytest warning is the existing `websockets.legacy` deprecation warning
from the environment; it is unrelated to this task.

## Compatibility and behavior review

- Existing R0–R3 domain, calendar, configuration, watchlist, historical,
  bridge, regime, replay, schema, model-boundary, and agent-runtime behavior
  remains covered by the full suite.
- Existing `RunContext`, `RunKey`, enum values, UTC validation, canonical JSON,
  immutable `FrozenMap`, and schema exports were preserved. The new storage
  values are additive and do not replace existing schemas or domain behavior.
- Automatic allocation creates only a staging r1 and returns the same stored
  context on duplicate scheduled invocation. Manual allocation uses the
  highest existing revision plus one, and no final revision is overwritten.
- The configured data root creates only `config`, `runs`, `reports`, `cache`,
  `diagnostics`, and `logs`. Run state is staged below the run-specific
  `.staging` directory; the final non-dot run directory appears only after
  the complete staging directory is renamed.
- Lease records contain the logical key, token, acquisition/heartbeat/expiry
  times, process ID, and host. Live expiry controls contention, heartbeats use
  atomic replacement, and expired lease bytes are copied to diagnostics before
  replacement.
- Checkpoints are immutable history files with the required run/stage/status,
  timestamps, artifact hashes, and resumability fields. Evidence freezing is
  one-way for a revision and a second cutoff explicitly requires a new
  revision.
- Publication writes canonical bundle/report bytes, fsyncs files and
  directories, recomputes SHA-256 values, rejects mismatches, atomically
  renames staging, and then atomically updates the index/latest records.
  Failed staging remains present, while `get_latest` and `get_report` expose
  only indexed, hash-verified final output.

## Security and scope review

- All public run-ID lookups validate the exact `premarket-YYYY-MM-DD-rN`
  grammar and reject absolute IDs, separators/traversal, control characters,
  malformed revisions, and non-positive revisions with `PATH_NOT_ALLOWED`.
- Every resolved target is checked to remain below the configured root,
  including symlinked storage parents. Tests verify an outside sentinel is
  unchanged.
- The filesystem adapter imports no provider, HTTP, socket, MCP, agent, or
  network transport capability. The host label uses existing process
  environment values only.
- No credentials, arbitrary caller paths, account/position/order/trading
  capability, evidence collection, analysis, synthesis, publication service,
  MCP, skills, or generic runtime behavior was added.
- The repository remains a storage primitive. It does not decide source
  authority, evidence freshness, financial calculations, scores, gates,
  sizing, or human approval state.

## Remaining risks and bounded limitations

- Revision allocation is intentionally a local filesystem primitive without a
  cross-process allocation lock. Leases protect logical run ownership, but a
  future concurrent allocator would need a separately reviewed atomic
  allocation protocol.
- The exact `allocate_revision` signature has no configuration input, so its
  fallback context uses explicit placeholder configuration/component versions;
  a later approved application composition layer must supply the real frozen
  configuration snapshot before Product A execution semantics are built.
- No provider refresh, packet loading, workflow resume engine, evidence
  collection, analysis, synthesis, or publication-service policy is included.
  Post-cutoff provider prohibition remains an application/workflow concern;
  this task only persists cutoff and checkpoint facts.
- An index-update failure after the final directory rename leaves a complete
  but unindexed orphan for recovery inspection. Hash-verified lookup methods
  do not expose it through `get_latest` or `get_report`, consistent with the
  required recovery boundary.
