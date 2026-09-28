# R9 Source-Role Quality Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Product A quality evaluation use the immutable source-role scope in each run's frozen source policy.

**Architecture:** `SourcePolicy` will own a closed, immutable role set and require the market-data and market-calendar roles. Domain quality evaluation will require health records for configured roles, hard-fail missing or unavailable required roles, and explicitly disable dependent capabilities for omitted optional roles. The collection quality boundary will receive the frozen policy; no source providers or network reads are added.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-28-r9-source-role-quality-design.md`

## Global Constraints

- Keep Product A research-only and within its currently approved source scope.
- Do not add SEC, macro, trading, MCP, scheduling, or provider capabilities.
- Derive quality scope from the immutable `ConfigurationSnapshot` stored in the run.
- Never synthesize a successful `SourceHealth` record.
- Required market-data and market-calendar failures remain global hard failures.
- Keep default tests offline and runtime dependencies empty.
- Commit each task after its focused tests pass; run full repository checks before feature review and delivery.

## Review Focus

- Duplicate, empty, mutable, or incomplete role tuples must be rejected; test in Task 1.
- A configured role with missing, optional, or mismatched health must fail closed; test in Task 2.
- An omitted macro or official-verification role must disable its mapped capabilities with `SOURCE_NOT_CONFIGURED` and produce `DEGRADED`; test in Task 2.
- Health or failures for a provider belonging to an omitted role must be rejected; test in Task 2.
- The frozen policy's disabled capability reasons must survive collection-quality evaluation and appear in packet/reduced-report inputs; test in Task 2.

---

### Task 1: Freeze the quality source-role declaration

**Files:**
- Modify: `src/finance_research_agent/domain/enums.py`
- Modify: `src/finance_research_agent/domain/policies.py`
- Modify: `config/examples/source-policy.yaml`
- Modify: all `SourcePolicy` construction sites in `tests/`
- Test: `tests/contracts/test_foundation_enums.py`
- Test: `tests/contracts/test_r3_configuration.py`

**Interfaces:**
- Produces: `SourceRole` enum values `MARKET_DATA="market-data"`, `MARKET_CALENDAR="market-calendar"`, `MACRO_CALENDAR="macro-calendar"`, `OFFICIAL_VERIFICATION="official-verification"`.
- Produces: required `SourcePolicy.quality_source_roles: tuple[SourceRole, ...]`.
- Validation: tuple only, unique values, and mandatory `MARKET_DATA` plus `MARKET_CALENDAR`; no default.

- [ ] **Step 1: Write failing role enum, policy, and snapshot tests.**

Add `test_source_role_values_are_closed_and_stable` to `tests/contracts/test_foundation_enums.py`. In `tests/contracts/test_r3_configuration.py`, add `test_source_policy_requires_market_data_and_calendar_roles`, `test_source_policy_rejects_duplicate_or_non_tuple_roles`, and `test_quality_source_roles_change_configuration_snapshot_hash`; update `test_all_shipped_policy_examples_load_with_stable_hashes` to assert the example roles are exactly `MARKET_DATA` and `MARKET_CALENDAR`. Assert frozen assignment is rejected.

- [ ] **Step 2: Run focused tests and confirm the expected failures.**

Run: `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/pytest tests/contracts/test_foundation_enums.py tests/contracts/test_r3_configuration.py -q`

Expected: failures because `SourceRole` and `quality_source_roles` do not exist and current `SourcePolicy` construction has no role declaration.

- [ ] **Step 3: Implement the enum and frozen `SourcePolicy` field.**

Update the example YAML and all test construction sites. Rely on existing configuration snapshot serialization and hashing; do not add a separate hash path.

- [ ] **Step 4: Run focused tests and static checks.**

Run the focused pytest command above, `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/ruff check src/finance_research_agent/domain/enums.py src/finance_research_agent/domain/policies.py tests/contracts/test_foundation_enums.py tests/contracts/test_r3_configuration.py`, and `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/mypy src/finance_research_agent/domain/enums.py src/finance_research_agent/domain/policies.py`.

- [ ] **Step 5: Analyze policy coverage.**

Measure statement and branch coverage for the new `SourceRole` and `SourcePolicy` validation. If branch instrumentation is unavailable, use standard-library statement tracing and report branch coverage as unavailable. Add focused tests for any uncovered validation path required by the spec.

- [ ] **Step 6: Run full checks, inspect, and commit this module.**

Run `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/pytest`, `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/ruff check .`, `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/mypy src`, and `git diff --check`; inspect the full diff and commit as `feat: freeze quality source roles in policy`.

#### Task 1 Module Review and Delivery Gate

- [ ] Obtain an independent review of the `SourceRole` and `SourcePolicy` module diff; resolve Critical and Important findings and rerun affected tests.
- [ ] Repeat full checks and policy coverage analysis after review fixes.
- [ ] Push this completed module as a PR, verify its diff, required checks, review state, and mergeability, then merge only when clean and verify the merge SHA on `origin/main`.
- [ ] Start Task 2 from the verified updated `origin/main`.

---

### Task 2: Apply configured roles through quality evaluation

**Files:**
- Modify: `src/finance_research_agent/domain/errors.py`
- Modify: `src/finance_research_agent/domain/quality.py`
- Modify: `src/finance_research_agent/application/collection_quality.py`
- Modify: direct callers and tests under `tests/unit/` and `tests/application/`
- Test: `tests/unit/test_quality.py`
- Test: `tests/application/test_collection_quality_service.py`
- Test: `tests/application/test_preparation_service.py`

**Interfaces:**
- Consumes: explicit `source_roles: tuple[SourceRole, ...]` in both `evaluate_capabilities` and `evaluate_data_quality`; no default.
- Produces: `ErrorCode.SOURCE_NOT_CONFIGURED` for capability restrictions caused by a role omitted from the frozen policy.
- Both functions reject non-tuples, duplicate roles, or a role set missing `MARKET_DATA` or `MARKET_CALENDAR`.
- Omitted `MACRO_CALENDAR` disables event-risk, setup, plan, sizing, and portfolio-heat capabilities; omitted `OFFICIAL_VERIFICATION` disables plan-draft capability.
- Collection signature: `evaluate_collected_market_data_quality(collection: MarketDataCollection, *, source_health: Sequence[SourceHealth], source_policy: SourcePolicy, risk_policy: RiskPolicy | None) -> DataQualityResult`.
- The collection adapter forwards exactly `source_policy.quality_source_roles`.
- Role mappings and source-provider alternatives remain closed domain mappings.

- [ ] **Step 1: Write failing tests for role-scoped quality.**

Add `test_quality_omitted_roles_disable_only_dependent_capabilities`, `test_quality_requires_health_for_every_configured_role`, `test_unavailable_market_data_or_calendar_role_fails_globally`, `test_quality_rejects_health_for_an_omitted_role`, and `test_quality_rejects_failures_for_an_omitted_role` to `tests/unit/test_quality.py`. Assert exact disabled capabilities and `SOURCE_NOT_CONFIGURED` reason codes, `CONFIGURATION_INVALID` for absent/non-required health, and provider error preservation for unavailable configured roles. Preserve the existing symbol-scoped versus global provider-failure tests after giving each an explicit role tuple. In `tests/application/test_collection_quality_service.py`, add `test_collection_quality_uses_frozen_source_roles`: rehydrate `SourcePolicy` from a `RunContext`'s frozen `ConfigurationSnapshot`, pass it with collection and matching health, and prove a different current configuration does not alter the result. In `tests/application/test_preparation_service.py`, add `test_staged_reduced_report_discloses_source_not_configured_capabilities` and assert packet/report states contain the explicit reason without prose claiming omitted checks ran.

- [ ] **Step 2: Run focused tests and verify they fail for the new behavior.**

Run: `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/pytest tests/unit/test_quality.py tests/unit/test_scoring.py tests/unit/test_setups.py tests/application/test_collection_quality_service.py tests/application/test_preparation_service.py -q`

Expected: failures because evaluators do not accept role scope, treat all four health roles as mandatory, lack `SOURCE_NOT_CONFIGURED`, and the collection adapter does not accept a frozen `SourcePolicy`.

- [ ] **Step 3: Implement role-aware quality evaluation.**

Require health only for configured roles; reject contradictory/out-of-scope health and failure inputs. Apply `SOURCE_NOT_CONFIGURED` to the existing dependent capability sets for omitted optional roles. Keep a failed configured market-data or market-calendar role on the current hard-fail path. Update every direct quality caller to pass an explicit role tuple. Change the collection adapter to receive `SourcePolicy` and forward its role tuple unchanged. Preserve existing packet and reduced-report capability-reason rendering.

- [ ] **Step 4: Run focused tests, full checks, and static analysis.**

Run the focused pytest command above, then `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/pytest`, `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/ruff check .`, `/Users/ben/Documents/MyProj/finance-research-agent/.venv/bin/mypy src`, and `git diff --check`.

- [ ] **Step 5: Analyze quality-module coverage.**

Measure statement and branch coverage for `quality.py` and `collection_quality.py`. If branch instrumentation is unavailable, use standard-library statement tracing and report branch coverage as unavailable. Add focused tests for any uncovered validation or role-mapping branch required by the spec.

- [ ] **Step 6: Inspect and commit this module.**

Inspect all quality call sites and failure mappings, then commit as `feat: scope quality evaluation to configured roles`.

#### Task 2 Module Review and Delivery Gate

- [ ] Obtain an independent review of the quality evaluator and collection adapter diff against the approved spec; resolve Critical and Important findings and rerun affected tests.
- [ ] Repeat full repository checks and focused quality coverage analysis after review fixes.
- [ ] Push this completed module as a PR, verify its diff, required checks, review state, and mergeability, then merge only when clean and verify the merge SHA on `origin/main`.

---

At each module gate, verify no provider, SEC/macro access, MCP, scheduling, or financial capability was added. Include that module's TDD evidence, focused coverage analysis, independent review results, and GitHub check state in its PR description.
