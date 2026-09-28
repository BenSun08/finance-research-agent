# Product A Run Initialization Implementation Plan

> **For agentic workers:** Execute each task with test-driven development. Each task ends with focused verification and its own commit. After Task 2, complete an independent code review and coverage analysis before pushing the feature branch and opening a PR.

**Goal:** Persist the validated configuration and component versions in the first immutable run context while preserving atomic revision assignment and resume behavior.

**Architecture:** Add an immutable, non-identity `RunContextSeed` in the domain. Change the repository allocator to accept that seed, invocation kind, and optional requested revision; the filesystem adapter assigns identity and persists the final context once under its existing market-date lock. The application run service will consume this boundary in a later R9 subtask.

**Tech Stack:** Python 3.12+, Pydantic strict domain models, pytest, Ruff, mypy, filesystem adapter.

**Spec:** `docs/superpowers/specs/2026-09-28-product-a-run-initialization-design.md`

## Global Constraints

- Use Python 3.12 or newer.
- Keep source code, tests, configuration, logs, reports, and technical documentation in English.
- Keep runtime dependencies empty until an approved milestone requires them; do not add dependencies for this slice.
- Keep `project.version` in `pyproject.toml`, package `__version__`, and current version documentation aligned at `0.5.0.dev0`.
- Preserve Product A's research-only scope; add no brokerage, model provider, MCP, scheduling, or new market-data capability.
- Persist no caller path, credential, provider payload, callback, or mutable collection in the run seed.
- Keep run ID/revision uniqueness and filesystem path confinement in the repository adapter.

## Review Focus

- A duplicate scheduled r1 receives the original frozen configuration even if current configuration differs; pin this in `test_scheduled_r1_retains_original_seed`.
- An exact unpublished manual revision resumes without replacing its snapshot; pin this in `test_requested_unpublished_manual_revision_is_reused`.
- A requested revision gap or published revision fails before staging changes; pin each case in `test_requested_manual_revision_rejects_gap` and `test_requested_manual_revision_rejects_published_run`.
- Concurrent manual allocations remain unique after moving identity inputs into the seed; retain and adapt `test_concurrent_manual_revision_allocations_are_unique`.
- Component policy versions cannot disagree with the frozen configuration snapshot; pin mismatch cases in `test_run_context_seed_rejects_policy_version_mismatch`.

---

### Task 1: Add the immutable run-context seed

**Files:**

- Modify: `src/finance_research_agent/domain/models.py`
- Modify: `tests/contracts/test_foundation_models.py`

**Interfaces:**

- Consumes: `ConfigurationSnapshot`, `ComponentVersions`, `DeliveryStatus`, `UtcDatetime`.
- Produces: strict immutable `RunContextSeed` with `market_date`, `invoked_at`, `delivery_status`, `configuration_snapshot`, and `component_versions` fields. Policy versions in `component_versions` must match the corresponding versions in `configuration_snapshot`.

- [ ] **Step 1: Write failing seed contract tests.**

  Test valid construction, strict extra-field rejection, frozen assignment, UTC timestamp validation, and mismatches for watchlist, regime, setup, risk, and source policy versions.

- [ ] **Step 2: Run the focused tests and confirm the expected failure.**

  Run: `pytest tests/contracts/test_foundation_models.py -q`

  Expected: FAIL because `RunContextSeed` is not defined.

- [ ] **Step 3: Implement `RunContextSeed` in `domain/models.py`.**

  Use the existing `StrictModel`, `ConfigurationSnapshot`, `ComponentVersions`, and enum types. Do not add run identity or revision fields; the repository allocates those.

- [ ] **Step 4: Run the focused tests and static checks.**

  Run: `pytest tests/contracts/test_foundation_models.py -q`

  Run: `ruff check src/finance_research_agent/domain/models.py tests/contracts/test_foundation_models.py`

  Run: `mypy src/finance_research_agent/domain/models.py`

  Expected: all tests pass and both static checks pass.

- [ ] **Step 5: Review this task's diff and commit it.**

  Run `git diff --check`, inspect the staged diff for unrelated changes, then commit as `feat: add immutable run context seed`.

### Task 2: Allocate revisions atomically from a seed

**Files:**

- Modify: `src/finance_research_agent/application/ports.py`
- Modify: `src/finance_research_agent/adapters/filesystem.py`
- Modify: `tests/integration/test_run_identity.py`
- Modify: `tests/unit/test_filesystem_store.py`

**Interfaces:**

- Consumes: `RunContextSeed`, `InvocationType`, optional positive `requested_revision`.
- Produces:

  ```python
  def allocate_revision(
      self,
      seed: RunContextSeed,
      invocation: InvocationType,
      requested_revision: int | None = None,
  ) -> RunContext: ...
  ```

- [ ] **Step 1: Write failing identity/allocation tests.**

  Cover: new context copies every seed field exactly; scheduled r1 reuses the original context when passed a different seed; manual calls without a requested revision increment; an exact existing unpublished requested revision is reused; only `max_existing + 1` may be newly requested; gaps, non-positive/non-integer revisions, scheduled explicit revisions, and published requested revisions fail without creating or changing staging state; concurrent manual allocations remain unique.

- [ ] **Step 2: Run the focused tests and confirm the expected failures.**

  Run: `pytest tests/integration/test_run_identity.py tests/unit/test_filesystem_store.py -q`

  Expected: FAIL because the allocator still accepts date/time inputs and cannot consume frozen configuration.

- [ ] **Step 3: Update the `RunRepository` port and filesystem allocator.**

  Under the existing per-date lock, preserve scheduled r1 idempotency and automatic manual `max + 1` allocation. For an explicit manual request, return the exact existing unpublished context; otherwise create only the next revision, rejecting gaps and published revisions. Build the complete `RunContext` from `RunContextSeed` and the assigned run ID/revision, then persist it once. Remove adapter-owned placeholder configuration/version construction. Do not change path confinement, checkpoint, lease, or publication behavior.

- [ ] **Step 4: Run focused tests and repository checks.**

  Run: `pytest tests/integration/test_run_identity.py tests/unit/test_filesystem_store.py -q`

  Run: `ruff check .`

  Run: `mypy src`

  Run: `pytest`

  Expected: all tests and checks pass.

- [ ] **Step 5: Perform coverage analysis and review the task diff.**

  Measure line/branch execution for `RunContextSeed` validation and the changed allocator paths. Report each validation branch as covered or uncovered; add a focused test for any uncovered branch required by the spec. Review all changed call sites and use `git diff --check`.

- [ ] **Step 6: Commit the tested allocator change.**

  Commit as `feat: allocate immutable run contexts atomically`.

### Feature review and delivery gate

- [ ] Request an independent review of both task commits against the approved design; resolve every actionable finding and rerun affected tests.
- [ ] Re-run full repository checks and confirm the final diff contains only the seed/allocation feature.
- [ ] Push `codex/r9-prepare-entrypoint`, open a PR with test, review, and coverage evidence, and attach the PR to this task.
- [ ] Check the PR diff and required CI. Merge only if review, coverage, CI, and mergeability are clean; then verify the merged commit on `main`.
