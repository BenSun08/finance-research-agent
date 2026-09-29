# Product A Run-Scoped Performance Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture, checkpoint, publish, and replay secret-free performance measurements for the approved Product A run path.

**Architecture:** A run-scoped `RunTelemetryRecorder` implements a provider request observer and measures named application stages with monotonic time. The safe HTTP client reports bounded request-attempt and consumed-response-byte totals; application checkpoints bind canonical telemetry snapshots by hash; normal, reduced, operational-failure publication, and replay preserve the same frozen snapshot.

**Tech Stack:** Python 3.12+, Pydantic strict models, immutable `FrozenMap`, existing `RunRepository` artifact/checkpoint ports, `SafeHttpClient`, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-29-product-a-performance-telemetry-design.md`

## Global Constraints

- Only configured `market-data` and `market-calendar` roles are measured.
- No SEC, macro, news, company-IR, production-model, MCP, scheduling, or brokerage capability is added.
- Raw URLs, query values, headers, credentials, response contents, symbols, prompts, report prose, and provider exception messages never enter telemetry.
- The 900,000 ms budget field records the §37.4 normal-provider p95 duration target; it never cancels work or changes publication eligibility.
- UTC time controls evidence cutoffs and deadlines; monotonic time controls durations.
- Telemetry does not influence financial values, data-quality decisions, eligibility, or publication validity.
- Each implementation task follows RED → GREEN, runs its focused tests, and commits its own changes.

## Review Focus

- Redirects and retries could undercount physical HTTP attempts; test each outbound send while preserving existing retry policy.
- Failed requests could leak exception text or omit attempted sends; test only numeric counts and closed errors reach telemetry.
- Resume could lose accumulated duration or double-count a completed stage; restore from the latest hashed snapshot and test both.
- A changed telemetry snapshot could be substituted during idempotent publication; bind and compare its canonical bytes and hash.
- Replay could consult a current clock/provider or accept malformed telemetry; test frozen parity and fail-closed behavior.

---

### Task 1: Add the run-scoped telemetry recorder and checkpoint artifact helpers

**Files:**
- Create: `src/finance_research_agent/application/performance_telemetry.py`
- Modify: `src/finance_research_agent/application/ports.py`
- Modify: `src/finance_research_agent/domain/models.py`
- Test: `tests/application/test_performance_telemetry.py`
- Test: `tests/contracts/test_performance_telemetry.py`

**Interfaces:**
- `ProviderRequestObserver.record_http_exchange(adapter: str, request_attempts: int, response_bytes: int) -> None`
- `RunTelemetryRecorder(*, monotonic_ns: Callable[[], int], initial: PerformanceTelemetry | None = None)`
- `RunTelemetryRecorder.measure_stage(stage: str) -> AbstractContextManager[None]`
- `RunTelemetryRecorder.record_http_exchange(adapter: str, request_attempts: int, response_bytes: int) -> None`
- `RunTelemetryRecorder.snapshot(*, research_packet_bytes: int = 0, validation_attempts: int = 0) -> PerformanceTelemetry`
- `load_checkpoint_telemetry(repository: RunRepository, checkpoint: RunCheckpoint) -> PerformanceTelemetry | None`
- `checkpoint_with_telemetry(repository: RunRepository, checkpoint: RunCheckpoint, telemetry: PerformanceTelemetry) -> RunCheckpoint`

- [x] **Step 1: Write failing tests** for exact stage accumulation, bounded provider counters, 900,000 ms target arithmetic including an over-target run, zero-filled `alpaca`/`market-calendar` metrics, strict adapter allowlisting, and checkpoint artifact hash round-trip.
- [x] **Step 2: Run the focused tests and confirm the intended missing-interface failures.**
- [x] **Step 3: Implement the recorder, observer protocol, canonical snapshot serialization, and hash-bound checkpoint helpers. Preserve actual elapsed time above the target and clamp remaining budget to zero.**
- [x] **Step 4: Run the focused tests and existing telemetry contract tests.**
- [x] **Step 5: Run Ruff and mypy on changed source files; inspect the diff for secret-bearing fields.**
- [x] **Step 6: Commit** as `feat: add run-scoped telemetry recorder`.

### Task 2: Measure approved outbound market-data attempts without changing request policy

**Files:**
- Modify: `src/finance_research_agent/adapters/http_client.py`
- Modify: `src/finance_research_agent/adapters/alpaca.py`
- Modify: `src/finance_research_agent/application/ports.py`
- Modify: `src/finance_research_agent/application/market_collection.py`
- Modify: `src/finance_research_agent/application/collection_service.py`
- Test: `tests/security/test_http_boundaries.py`
- Test: `tests/adapters/test_alpaca.py`
- Test: `tests/application/test_market_collection.py`

**Interfaces:**
- `SafeHttpClient.request(..., telemetry_observer: ProviderRequestObserver | None = None) -> SafeResponse`
- `MarketDataProvider.fetch_instruments(..., *, telemetry_observer: ProviderRequestObserver | None = None)`
- `MarketDataProvider.fetch_daily_bars(..., telemetry_observer: ProviderRequestObserver | None = None)`
- `MarketDataProvider.fetch_premarket_observations(..., telemetry_observer: ProviderRequestObserver | None = None)`
- `collect_market_data_for_run(..., telemetry: RunTelemetryRecorder | None = None) -> MarketDataCollection`

- [x] **Step 1: Write failing transport tests** asserting retries and redirects count every HTTP send, accepted response bytes equal `len(SafeResponse.content)`, and request/response data never reaches the observer. Existing source request deadlines and retry behavior must remain unchanged.
- [x] **Step 2: Run those tests and confirm the observer/deadline behavior is missing.**
- [x] **Step 3: Implement optional observer reporting in `SafeHttpClient`; invoke it for success and transport failure using only adapter ID, attempt count, and consumed body bytes.**
- [x] **Step 4: Thread the optional observer through the Alpaca adapter and run-scoped collection path. Keep existing callers source-compatible with `None` defaults.**
- [x] **Step 5: Run focused adapter and collection tests, including existing retry, redirect, response-size, and no-network tests.**
- [x] **Step 6: Run Ruff and mypy on changed source files; review that telemetry covers only the approved adapter.**
- [x] **Step 7: Commit** as `feat: measure bounded market-data requests`.

### Task 3: Record stage, packet, quality, and validation measurements at checkpoints

**Files:**
- Modify: `src/finance_research_agent/application/run_service.py`
- Modify: `src/finance_research_agent/application/collection_service.py`
- Modify: `src/finance_research_agent/application/quality_pipeline.py`
- Modify: `src/finance_research_agent/application/preparation_service.py`
- Modify: `src/finance_research_agent/application/publication_service.py`
- Modify: `src/finance_research_agent/application/quality_checkpoint.py`
- Modify: `src/finance_research_agent/adapters/filesystem.py`
- Test: `tests/application/test_collection_service.py`
- Test: `tests/application/test_quality_pipeline.py`
- Test: `tests/application/test_preparation_service.py`
- Test: `tests/application/test_prepare_premarket_run.py`
- Test: `tests/application/test_publication_service.py`
- Test: `tests/application/test_run_performance_telemetry.py`

**Interfaces:**
- `RunTelemetryRecorder.restore()` loads the latest verified, content-addressed telemetry snapshot before each subsequent stage; `load_latest_checkpoint_telemetry()` scans checkpoint history backward.
- Each stage measures only its own active work and binds a content-addressed snapshot in the checkpoint it writes. Preparation creates the initial `CONFIG_FROZEN` telemetry checkpoint.
- Validation attempts are derived from `VALIDATING` checkpoint history; packet bytes are derived from `_canonical_packet_bytes(packet)`.
- The filesystem checkpoint transition validator permits one hash-named telemetry artifact while preserving the existing business-artifact transition contract.

- [x] **Step 1: Write failing recovery tests** showing elapsed stages and request counters survive resume exactly once, packet bytes match the canonical staged artifact, and validation attempts match persisted checkpoints.
- [x] **Step 2: Run focused tests and confirm telemetry is absent from stage checkpoints.**
- [x] **Step 3: Integrate restore/measure/snapshot/checkpoint binding for run preparation, collection, quality evaluation, packet assembly, and validation.**
- [x] **Step 4: Run the focused application suites and their resume, failure, concurrency, and cutoff cases.**
- [x] **Step 5: Run Ruff and mypy on changed source files; inspect checkpoint compare-and-swap and artifact hash preservation.**
- [ ] **Step 6: Commit** as `feat: checkpoint Product A run telemetry`.

### Task 4: Freeze telemetry through publication and zero-network replay

**Files:**
- Modify: `src/finance_research_agent/application/publication_service.py`
- Modify: `src/finance_research_agent/application/operational_report.py`
- Modify: `src/finance_research_agent/application/replay_service.py`
- Modify: `src/finance_research_agent/adapters/filesystem.py`
- Test: `tests/application/test_publication_service.py`
- Test: `tests/application/test_operational_report.py`
- Test: `tests/application/test_replay_service.py`

**Interfaces:**
- All publication paths load telemetry from the latest verified checkpoint; no public caller may substitute a telemetry value.
- New bundles carry the canonical telemetry value and its checkpoint-bound digest. Replay verifies the value against that digest and the indexed bundle hash; legacy bundles without telemetry remain replayable and report `telemetry=None`.
- `PUBLICATION` measures deterministic report rendering before the immutable telemetry snapshot and atomic filesystem publication are persisted; the resulting snapshot is staged, checkpoint-bound, and included in the bundle.

- [x] **Step 1: Write failing tests** for normal, reduced, and operational-failure publication; atomic retry with identical telemetry; changed-snapshot rejection; replay parity; malformed telemetry/hash rejection; and zero provider/clock calls during replay.
- [x] **Step 2: Run focused tests and confirm telemetry is missing from frozen publication/replay values.**
- [x] **Step 3: Persist the checkpointed snapshot in every immutable published bundle and validate it during retries and replay.**
- [x] **Step 4: Run publication, operational-report, and replay suites.**
- [x] **Step 5: Run Ruff and mypy on changed source files; inspect every serialization boundary for secrets and nondeterministic data.**
- [ ] **Step 6: Commit** as `feat: persist telemetry in published artifacts`.

### Task 5: Verify, review, measure coverage, and deliver the R9 telemetry feature

**Files:** all files changed by Tasks 1–4.

- [ ] **Step 1: Run the full repository verification:** `pytest -p no:cacheprovider -q`, `ruff check --no-cache .`, and `mypy --cache-dir /tmp/finance-agent-r9-mypy-cache src`.
- [ ] **Step 2: Run focused line and branch coverage** for all changed application, domain, adapter, and replay modules; record uncovered branches and add justified tests first where safety or persistence behavior is untested.
- [ ] **Step 3: Perform an independent whole-feature code review** against this design, the approved R9 plan, the source-scope contract, secret-redaction rules, and mutation cases.
- [ ] **Step 4: Fix only review findings with failing tests first; rerun the focused tests, coverage, and full repository checks. Commit each accepted remediation separately.**
- [ ] **Step 5: Review the complete diff and ensure the worktree is clean.**
- [ ] **Step 6: Push `codex/r9-publication`, open one PR for the R9 telemetry feature, and attach it to this task.**
- [ ] **Step 7: Check PR CI, formal reviews, and inline threads against the exact pushed head. Merge only when green and review-clean, then fetch and verify the merge commit on `main`.**
