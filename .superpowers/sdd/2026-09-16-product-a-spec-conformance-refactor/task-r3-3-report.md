# R3 Task 3 Implementation Report

## Status

Complete as a bounded local implementation at the reviewed R2 BASE
`b233795d963731cba05b7f68c52b44eba6717830`. No push, merge, R3 Task 4/5, or
R4+ work was performed.

The implementation adds strict immutable policy/configuration contracts, fixed
five-file YAML loading, immutable configuration snapshots with per-policy
hashes, and optimistic version-checked atomic watchlist transactions. The
existing R1/R2 regime dataclass, formulas, historical behavior, replay
semantics, provider boundary, and no-brokerage/security guards remain in
place.

## Commit

The commit is recorded after this report is staged:

`673c65e` — focused implementation commit for R3 Task 3.

## Changed files

- `src/finance_research_agent/domain/policies.py`
  - Added frozen/strict `WatchlistItem`, `WatchlistConfig`, `RiskPolicy`,
    `SetupPolicy`, `SourcePolicy`, and `AppConfiguration` values.
  - Added ticker, bounded slug, English printable prose, HTTPS-source,
    decimal, version, duplicate, and watchlist-size validation.
  - Added canonical compact sorted-key UTF-8 SHA-256 model hashing.
  - Kept the existing `domain.regime.RegimePolicy` as the compatibility
    calculation contract rather than replacing its semantics.
- `src/finance_research_agent/application/ports.py`
  - Added provider-neutral `ConfigurationRepository` and
    `WatchlistRepository` protocols.
- `src/finance_research_agent/application/config_service.py`
  - Added immutable snapshot construction from an injected repository,
    including all five policy copies, declared versions, per-policy hashes,
    aggregate content identity, and fixed radar universe.
- `src/finance_research_agent/application/watchlist_service.py`
  - Added `list`, `upsert`, and `remove` with typed stale-version rejection
    and versioned change summaries.
- `src/finance_research_agent/adapters/yaml_config.py`
  - Added fixed-filename `yaml.safe_load` configuration adapter and atomic
    YAML watchlist repository with staging, reread/validation, file fsync,
    `os.replace`, and parent-directory fsync.
- `config/examples/{watchlist,risk,regime,setup,source}-policy.yaml`
  - Added synthetic, non-operational, English policy examples.
- `tests/contracts/test_r3_configuration.py`
  - Added focused coverage for the 30-name bound, example loading/hash
    stability, optimistic versioning, stale mutation rejection, and detached
    snapshot contents.
- `src/finance_research_agent/domain/models.py`
  - Extended `ConfigurationSnapshot` compatibly with optional immutable policy
    copies, policy hashes, and radar universe fields.
- `schemas/configuration-snapshot.schema.json` and
  `schemas/run-context.schema.json`
  - Regenerated deterministically for the intentional R3 snapshot extension
    and its existing `RunContext` consumer.
- `pyproject.toml`
  - Added the first required runtime dependency, `PyYAML==6.0.3`, and the
    corresponding strict-mypy development stub dependency,
    `types-PyYAML>=6.0.12,<7`.

## TDD evidence

### RED

The first focused test was written before the policy module existed. The first
run was:

`../../.venv/bin/pytest -q tests/contracts/test_r3_configuration.py`

It failed during collection with:

`ModuleNotFoundError: No module named 'finance_research_agent.domain.policies'`

This was the expected missing-production-boundary failure. After the minimal
policy boundary existed, the focused test passed, and subsequent focused tests
were added for the repository/service behavior.

### GREEN

Focused final run:

`PYTHONPATH=src ../../.venv/bin/pytest -q tests/contracts/test_r3_configuration.py`

Result: `4 passed`.

Full final run:

`PYTHONPATH=src ../../.venv/bin/pytest -q`

Result: `1113 passed, 1 warning`.

The sole warning is the pre-existing `websockets.legacy` deprecation warning
from the virtual environment.

## Full verification

- `PYTHONPATH=src ../../.venv/bin/pytest -q` — `1113 passed, 1 warning`.
- `../../.venv/bin/ruff check .` — passed.
- `../../.venv/bin/mypy src` — passed with no issues in 41 source files.
- `git diff --check` — passed with no output.
- Checked-in schema generation was run with
  `PYTHONPATH=src ../../.venv/bin/python -m finance_research_agent.schema_export --output-dir schemas`.
- The schema contract suite passed as part of the full pytest run.

## Compatibility and security review

- Existing `ConfigurationSnapshot` callers remain valid because the new
  policy-copy/hash/radar fields are optional/defaulted; generated schemas were
  updated rather than weakening schema checks.
- The existing `RegimePolicy` constructor, defaults, field types, validation,
  `required_symbols`, weights, thresholds, and calculation formulas were not
  changed. YAML regime values are adapted into that existing dataclass at the
  adapter boundary.
- Application code depends only on provider-neutral repository protocols. It
  does not import `finance_research_agent.adapters`, Alpaca, network clients,
  or the generic agent runtime.
- URL validation uses the existing provider-neutral Pydantic `HttpUrl`
  boundary and explicit HTTPS, credential, fragment, and traversal checks;
  no `urllib`, transport, or dynamic-import escape hatch was introduced.
- YAML is loaded only through `yaml.safe_load` against the five fixed filenames;
  callers cannot supply arbitrary per-policy paths.
- Watchlist mutation validates the current version before writing. Stale
  versions raise `ConfigurationVersionConflict` and do not modify the file.
- The shipped risk example has sizing disabled, null capital/risk/heat values,
  five maximum concurrent drafts, no fractional units, quantity increment
  `0.001`, and the required regime multipliers. The watchlist example is
  synthetic and contains no holdings, account, cost-basis, or broker data.
- Full existing architecture, model-boundary, Alpaca-boundary, schema,
  bridge, replay, and regime tests remained green.

## Remaining risks and bounded limitations

- `RegimePolicy` remains the established R1/R2 frozen dataclass and is
  represented in the R3 `AppConfiguration`/snapshot through an adapter and
  immutable serialized copy. A future migration may introduce a standalone
  serialized regime-policy model, but that is outside this task because it
  would risk changing established R1/R2 semantics.
- The R3 task does not implement market-time/run identity, leases,
  checkpoints, filesystem run storage, evidence collection, current quotes,
  events, analysis, synthesis, publication, MCP, skills, or provider
  integration. Those remain explicitly deferred.
- The existing virtual-environment warning is external to this change and does
  not affect test success.

## R3 Task 3 fix round — reviewer findings

### Status

Complete. This bounded fix round remains strictly within R3 Task 3. It does
not start R3 Task 4/5 or R4+, and it performed no push, merge, or subagent
dispatch.

### Fix commit

`af19b7d` — `fix: harden R3 configuration transactions`

### Changed files

- `src/finance_research_agent/application/config_service.py`
  - Added `ConfigService.from_directories(source, staging_root)` through an
    adapter-side registered repository factory; the application module still
    has no adapter import.
  - Preserved complete immutable policy projections while mapping
    `ConfigurationSnapshot.file_hashes` to all five fixed YAML filenames.
  - Projected the full fixed/configurable radar universe independently from
    the R1/R2 calculation-input universe.
- `src/finance_research_agent/application/ports.py`
  - Added the provider-neutral directory factory and repository-owned
    `replace_if_version` compare-and-swap boundary.
- `src/finance_research_agent/application/watchlist_service.py`
  - Uses the CAS boundary for upsert/remove and validates removal symbols
    before reading or mutating configuration.
- `src/finance_research_agent/adapters/yaml_config.py`
  - Added adapter-side service-factory registration, fixed-root validation,
    all-five policy hashes, same-directory staging, reread/validate before
    file fsync, atomic replace, parent-directory fsync, and per-target
    repository locks.
- `src/finance_research_agent/domain/policies.py`
  - Added bounded ticker/tag/prose/source validation, percent-decoded URL
    traversal rejection, source collection/range validation, safe risk
    multiplier ranges, exact setup score/penalty validation, and immutable
    collection input copying.
- `src/finance_research_agent/domain/regime.py`
  - Added DIA plus optional Treasury, dollar, gold, oil, and volatility radar
    symbols without changing `required_symbols` or existing formulas.
- `config/examples/regime-policy.yaml`
  - Added the fixed-radar DIA and cross-asset/volatility symbols.
- `config/examples/setup-policy.yaml`
  - Updated to the six Product A score weights and exact four penalty names.
- `tests/contracts/test_r3_configuration.py`
  - Updated the existing hash expectation for all five policies.
- `tests/contracts/test_r3_task3_fixes.py`
  - Added regression coverage for factory/path boundaries, five filename
    hashes, radar projection, YAML-format-independent hashes, immutable
    nested snapshots, strict field/security grammars, staging/fsync order,
    concurrent CAS behavior, and remove validation.

### TDD evidence

RED was observed before production fixes with:

`../../.venv/bin/pytest -q tests/contracts/test_r3_task3_fixes.py`

Result: `14 failed, 14 passed`.

The failures were the expected missing/incomplete behaviors: absent
`ConfigService.from_directories`, incomplete ticker/tag/source/policy
validation, incomplete hashes/radar projection, non-sibling staging and
incorrect durability order, non-atomic concurrent mutation, and missing
remove-symbol validation.

Focused GREEN:

`../../.venv/bin/pytest -q tests/contracts/test_r3_task3_fixes.py`

Result: `28 passed`.

R3 compatibility plus fix regression suite:

`../../.venv/bin/pytest -q tests/contracts/test_r3_configuration.py tests/contracts/test_r3_task3_fixes.py`

Result: `32 passed`.

### Final verification

- `../../.venv/bin/pytest -q` — `1141 passed, 1 warning`.
- `../../.venv/bin/ruff check .` — `All checks passed!`.
- `../../.venv/bin/mypy src` — `Success: no issues found in 41 source files`.
- `git diff --check` — passed with no output.

The one pytest warning remains the pre-existing
`websockets.legacy` deprecation warning from the virtual environment.

### Compatibility and security review

- The application-to-adapter dependency guard remains green. The application
  receives a provider-neutral factory registration; only the adapter imports
  and registers its YAML repository factory.
- R1/R2 regime calculation inputs, constructor compatibility, `required_symbols`,
  weights, thresholds, formulas, bridge behavior, replay behavior, and
  security boundaries remain green. The new `radar_universe` is a separate
  projection used only for configuration snapshots.
- Snapshot `file_hashes` now contains exactly `watchlist.yaml`,
  `risk-policy.yaml`, `regime-policy.yaml`, `setup-policy.yaml`, and
  `source-policy.yaml`; hashes are canonical validated-model SHA-256 values,
  independent of YAML formatting.
- Nested snapshot mappings and arrays remain detached and immutable through
  `FrozenMap`/tuple projections.
- Strict validation rejects unknown fields, duplicate symbols, unbounded
  tickers/tags/collections, non-English or non-printable stored prose,
  controls/bidi/zero-width characters, ticker confusables, unsafe URL
  credentials/fragments/ports/schemes, direct and encoded traversal, unsafe
  multipliers, negative source limits/budgets, invalid setup scores, and
  non-exact penalty sets.
- Watchlist writes use a same-directory staging sibling, validate the staged
  bytes before file fsync, atomically replace the target, and fsync the parent
  directory. Repository-owned locking and version comparison ensure one
  winner for concurrent callers using the same target.

### Remaining risks and bounded limitations

- The lock is process-local (`threading.RLock`) and protects concurrent
  callers through repository instances in the running process. A future
  cross-process transaction design would require a separately approved
  filesystem-lock/lease boundary; no such R3 Task 3 capability was added.
- `ConfigService.from_directories` requires the trusted adapter-side factory
  registration or an explicitly injected factory; it does not discover
  adapters dynamically.
- R3 Task 4/5 and R4+ capabilities remain deferred, including market time,
  run identity, leases/checkpoints, filesystem run storage, evidence
  collection, current quotes, events, analysis, synthesis, publication, MCP,
  skills, scheduling, and production model integration.

## R3 Task 3 fix round 2 — scoped re-review findings

### Status

Complete. This round addresses exactly the two new Important findings. It
remains strictly within R3 Task 3 and performed no Task 4/5 or R4+ work, no
subagent dispatch, no push, and no merge.

### Fix commit

`38b3562` — `fix: remove configuration bootstrap side effect`

### Changes

- `src/finance_research_agent/application/config_service.py`
  - Added a trusted fixed-file `DirectoryConfigurationRepository` bootstrap
    owned by the application boundary.
  - `ConfigService.from_directories(source, staging_root)` now works when a
    caller imports only `finance_research_agent.application.config_service`;
    optional repository-factory injection remains available for composition.
  - No adapter import, dynamic import, or registration side effect is used.
- `src/finance_research_agent/adapters/yaml_config.py`
  - Retained the adapter public `load_configuration` compatibility export and
    `YamlConfigurationRepository` surface as thin wrappers over the trusted
    bootstrap implementation.
  - Removed adapter-side class registration.
- `src/finance_research_agent/domain/policies.py`
  - Normalized both `/` and `\\` separators after every percent-decoding pass
    before checking `.`/`..` traversal segments.
- `tests/contracts/test_r3_task3_fixes.py`
  - Added a fresh-process exact application-only import/use regression.
  - Added `https://example.test/%5c..%5cprivate` to URL traversal coverage.

### TDD evidence

RED command:

`../../.venv/bin/pytest -q tests/contracts/test_r3_task3_fixes.py -k 'application_only or official_source_url_security'`

Result: `2 failed, 7 passed, 21 deselected`.

The two failures were the expected public-application bootstrap failure and
the accepted percent-decoded backslash traversal URL.

Focused GREEN command:

`../../.venv/bin/pytest -q tests/contracts/test_r3_task3_fixes.py -k 'application_only or official_source_url_security'`

Result: `9 passed, 21 deselected`.

Combined focused R3 configuration/fix suite:

`../../.venv/bin/pytest -q tests/contracts/test_r3_task3_fixes.py tests/contracts/test_r3_configuration.py`

Result: `34 passed`.

### Final verification

- `../../.venv/bin/pytest -q` — `1143 passed, 1 warning`.
- `../../.venv/bin/ruff check .` — `All checks passed!`.
- `../../.venv/bin/mypy src` — `Success: no issues found in 41 source files`.
- `git diff --check` — passed with no output.

The one pytest warning remains the pre-existing
`websockets.legacy` deprecation warning from the virtual environment.

### Compatibility and security review

- A subprocess test imported only the public application service and verified
  that no `finance_research_agent.adapters` module was loaded before calling
  `ConfigService.from_directories` successfully.
- The application-to-adapter dependency guard and no-dynamic-import security
  guard remain green.
- Existing adapter imports and the public adapter `load_configuration` helper
  remain compatible.
- Official-source URL validation now rejects direct and nested percent-decoded
  traversal using either slash separator, including encoded backslash forms.

### Remaining risks and bounded limitations

- The default application bootstrap intentionally supports only the five fixed
  local YAML files; alternate repository implementations still require an
  explicitly injected provider-neutral factory.
- The process-local watchlist lock and all Task 4/5 and R4+ deferrals from the
  prior fix round remain unchanged.
