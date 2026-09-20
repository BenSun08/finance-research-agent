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

## R3 Task 5 fallback review-fix round

This round fixed the review findings without broadening the R3 Task 5 scope.
The focused fix commit is `4ef9a7f` (`fix: harden R3 Task 5 storage
boundaries`), based on the prior implementation/report commits
`29ea648`/`d19bf5c`. No push, merge, subagent dispatch, R4+ work, provider,
MCP, skill, scheduling, brokerage, execution, or other capability work was
started.

### Findings fixed

- C1: `acquire_lease()` now claims the per-market-date lease under a local
  process/file lock, so the live-lease check and replacement are one critical
  section. A two-contender test asserts exactly one `acquired` result and one
  `held` result.
- I1: publication loads the staged `run.json` and rejects a bundle whose
  complete `RunContext` differs, even when the run ID is the same.
- I2: after the persisted evidence cutoff, only `AWAITING_SYNTHESIS` or
  `VALIDATING` checkpoints with matching cutoff, matching execution status,
  and exactly the `research_packet` artifact hash are accepted. Collection,
  analysis, mismatched cutoff, and unrelated artifact checkpoints require a
  new revision.
- I3: run context, checkpoint files and nested directories, frozen evidence,
  report, bundle, report index, and latest pointer reads now resolve through
  the root-confinement helper. Symlinked-file, nested-checkpoint-file, report
  index, and storage-parent escape tests verify `PATH_NOT_ALLOWED` and an
  unchanged outside sentinel.
- I4: an index/latest failure after the final rename quarantines the complete
  directory under diagnostics as a recoverable orphan. Final loads and bundle
  loads require an indexed, hash-verified publication, so unindexed finals and
  quarantined orphans are not exposed.
- I5: publication now requires a declared Markdown SHA-256 and compares it to
  the canonical UTF-8 report bytes before rename; mismatch and missing-hash
  tests leave staging unpublished.

### Exact TDD evidence

The first RED run after adding the fallback regression tests was:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py --disable-warnings --tb=short
7 failed, 21 passed in 0.99s
```

The seven failures were the post-cutoff checkpoint constraint, the undeclared
index-update failure hook exposed by normal publication, unindexed-final
visibility, and the three symlink read-path checks (report, bundle, and
report-index). The RED failures were all observed before the corresponding
production fixes.

The focused GREEN run after the fixes was:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py --disable-warnings --tb=short
28 passed in 0.74s
```

### Final verification evidence

The worktree does not contain a local `.venv`; `../../.venv/bin/...` is the
repository environment from this worktree. The requested checks completed as
follows:

```text
../../.venv/bin/pytest -q --tb=short
1199 passed, 1 warning in 17.21s

../../.venv/bin/ruff check .
All checks passed!

../../.venv/bin/mypy src
Success: no issues found in 44 source files

git diff --check
<no output; exit 0>
```

The one pytest warning is the existing environment warning from
`websockets.legacy`; it is unrelated to this storage fix.

Correction to the earlier report: its statement that `git diff --check`
validated the committed `29ea648` result was too broad. A clean post-commit
`git diff --check` only proves that the current worktree has no unchecked
unstaged diff; it does not independently validate a commit’s patch. In this
round, `git diff --check` was run against the uncommitted fix changes and
returned no output, and `git diff --cached --check` also returned no output
immediately before commit `4ef9a7f`. Those are the relevant diff-check
evidence for this fix.

### Files changed in this round

- `src/finance_research_agent/adapters/filesystem.py`
- `tests/unit/test_filesystem_store.py`
- this report

The final concern is limited to the pre-existing `websockets.legacy`
deprecation warning. The full suite is green, the fix is committed locally,
and the work stops at the R3 Task 5 review gate.

## R3 Task 5 fallback review-fix round 2

The second scoped review round fixed the two remaining Important findings in
local commit `768aeea` (`fix: bind frozen packet checkpoints`), on top of
`4ef9a7f` and `c1609c8`. No existing commit or clean-state change was reset or
discarded, and no push, merge, subagent dispatch, R4+ work, provider, MCP,
skill, scheduling, brokerage, execution, or other capability work was started.

### Findings fixed

- Post-cutoff checkpoint validation now requires `resumable=False`, limits
  later checkpoints to `AWAITING_SYNTHESIS` or `VALIDATING` with matching
  execution status, and requires exactly one `research_packet` hash equal to
  the most recently stored packet hash from the same run. Collection/resume,
  replacement packet hashes, and resumable later-validation checkpoints are
  rejected with the new-revision cutoff error.
- Publication now reads an existing report index through `_read_confined`
  instead of raw `is_file()`/`read_bytes()` calls. A symlink escape raises
  `PathNotAllowedError`; if the final directory has already been renamed, it
  is quarantined as a diagnostic orphan before the typed error is propagated.

### Exact TDD evidence

After adding the round-2 regression tests and correcting one test-fixture
directory setup error, the RED focused run was:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py --disable-warnings --tb=short
2 failed, 28 passed in 0.90s
```

The two expected RED failures were the still-accepted resumable/hash-replaced
post-cutoff validation and the publication-time index symlink escape. The
earlier setup-only `FileNotFoundError` was corrected before recording this RED
result and was not treated as feature evidence.

The focused GREEN run after the implementation was:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py --disable-warnings --tb=short
30 passed in 0.75s
```

### Final verification evidence

The final full verification after the round-2 implementation was:

```text
../../.venv/bin/pytest -q --tb=short
1201 passed, 1 warning in 20.92s

../../.venv/bin/ruff check .
All checks passed!

../../.venv/bin/mypy src
Success: no issues found in 44 source files

git diff --check
<no output; exit 0>
```

The one warning remains the pre-existing `websockets.legacy` deprecation
warning from the environment. `git diff --cached --check` also returned no
output immediately before commit `768aeea`.

## R3 Task 5 fallback review-fix round 3

Round 3 closed the remaining `EVIDENCE_FROZEN` post-cutoff checkpoint bypass
in local commit `dbff717` (`fix: close frozen checkpoint bypass`), on top of
`768aeea` and `242f637`. No existing commit or clean-state change was reset or
discarded, and no push, merge, subagent dispatch, R4+ work, provider, MCP,
skill, scheduling, brokerage, execution, or other capability work was started.

### Finding fixed

All post-cutoff checkpoint stages, including `EVIDENCE_FROZEN`, now reject
`resumable=True` and replacement packet hashes. The only permitted
`EVIDENCE_FROZEN` form is the exact immutable freeze record generated by
`freeze_evidence()`—matching cutoff and execution status, `resumable=False`,
and no artifact hashes. Valid later `AWAITING_SYNTHESIS`/`VALIDATING`
checkpoints retain the previously stored packet-hash binding and must also be
non-resumable.

### Exact TDD evidence

The RED focused run after adding the `EVIDENCE_FROZEN` bypass regression test
was:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py --disable-warnings --tb=short
1 failed, 30 passed in 0.98s
```

The failure was the expected acceptance of a post-cutoff `EVIDENCE_FROZEN`
checkpoint carrying `resumable=True` and a replacement `research_packet` hash.

The GREEN focused run after the minimal validation fix was:

```text
../../.venv/bin/pytest -q tests/unit/test_filesystem_store.py tests/integration/test_run_identity.py --disable-warnings --tb=short
31 passed in 0.67s
```

### Final verification evidence

The required final checks after the round-3 implementation were:

```text
../../.venv/bin/pytest -q --tb=short
1202 passed, 1 warning in 17.17s

../../.venv/bin/ruff check .
All checks passed!

../../.venv/bin/mypy src
Success: no issues found in 44 source files

git diff --check
<no output; exit 0>
```

The one warning remains the pre-existing `websockets.legacy` deprecation
warning from the environment. `git diff --cached --check` also returned no
output immediately before commit `dbff717`.
