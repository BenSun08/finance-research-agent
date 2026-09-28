# R9 Evidence Cutoff Lifecycle Implementation Plan

> **For agentic workers:** Use the repository Change Process and test-driven development. Steps use checkbox syntax to track the implementation.

**Goal:** Allow live evidence to be collected after invocation while binding one immutable evidence cutoff before packet construction.

**Architecture:** A new run starts with an unbound cutoff. The filesystem repository binds the cutoff once when evidence is frozen and returns it in the loaded run snapshot; run identity and configuration remain unchanged. Packet and publication services continue to require a bound cutoff.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, filesystem repository.

**Spec:** `docs/superpowers/specs/2026-08-19-ai-market-research-agent-premarket-design.md` §11 and §11.2; `docs/superpowers/plans/2026-09-16-product-a-spec-conformance-refactor.md` R3 and R9.

## Global Constraints

- No evidence retrieved after the live run cutoff may enter that revision.
- Configuration, run identity, and checkpoint history remain immutable and deterministic.
- Keep Product A research-only and offline-testable; do not add provider, scheduling, MCP, or execution capabilities.
- Do not change historical replay semantics.

## Review Focus

- Evidence collected after invocation but before freeze remains admissible: assert run allocation has no cutoff and freeze binds the supplied collection-completion instant.
- Reopening stored state preserves the same cutoff: assert a fresh repository instance loads an equal frozen `RunContext`.
- Freeze cannot be moved or repeated: assert a second freeze fails and leaves original state intact.
- Packet and publication cannot consume an unbound context: assert failure occurs before writing artifacts.
- Existing seeded/published fixtures retain their already-bound cutoff and output behavior.

### Task 1: Allocate Runs Before the Evidence Cutoff Is Known

**Files:** `domain/models.py`, `adapters/filesystem.py`, `tests/application/test_prepare_premarket_run.py`, focused model tests.

- [x] Add a failing test that a new allocated `RunContext.evidence_cutoff_at` is `None` while `invoked_at` stays fixed.
- [x] Run the focused test and confirm it fails because allocation binds invocation time as the cutoff.
- [x] Make `RunContext.evidence_cutoff_at` optional and allocate it unbound.
- [x] Run focused tests and confirm new allocations are unbound while directly constructed frozen contexts remain valid.
- [x] Commit this task with its tests.

### Task 2: Bind the Cutoff Exactly Once at Evidence Freeze

**Files:** `adapters/filesystem.py`, `tests/unit/test_filesystem_store.py`.

- [x] Add failing tests that freeze rejects a cutoff before invocation, accepts a later cutoff, survives reload with `StoredRun.evidence_cutoff_at == StoredRun.run.evidence_cutoff_at`, and rejects rebinding.
- [x] Run the focused tests and confirm the cutoff is currently not bound to the loaded run.
- [x] Overlay the persisted freeze timestamp onto the loaded immutable run snapshot; preserve old pre-freeze fixture compatibility; reject cutoff timestamps before invocation.
- [x] Run focused storage tests and existing freeze/checkpoint tests.
- [ ] Commit this task with its tests.

### Task 3: Enforce the Bound Cutoff at Artifact Boundaries

**Files:** `domain/packets.py`, `application/preparation_service.py`, `application/publication_service.py`, `application/reduced_report.py`, focused packet/publication tests.

- [ ] Add failing tests that an unbound run cannot form or stage a research packet or publish a report.
- [ ] Run focused tests and confirm failures occur before artifact writes.
- [ ] Add explicit bound-cutoff guards at the packet, staging, publication, and rendering boundaries while preserving frozen-run identity checks.
- [ ] Run the focused suites, then full `pytest`, `ruff check .`, `mypy src`, and `git diff --check`.
- [ ] Review the final diff and coverage for every new branch, then commit this task.

## Completion Gate

Run an independent code review against the base/head diff. Fix all Critical and Important findings, rerun relevant verification, push the branch, open a PR, verify GitHub checks/reviews, and merge only when clean. Confirm the final merged commit is on `origin/main` before continuing R9.
