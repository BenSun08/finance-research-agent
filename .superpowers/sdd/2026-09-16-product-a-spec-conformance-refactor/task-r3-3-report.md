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

`PENDING_COMMIT_SHA`

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
