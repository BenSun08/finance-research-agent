# Release readiness and operator checks

This guide describes the checked-in R11 release gates and the bounded local
checks around them. The authoritative commands and named CI steps are in
[`ci.yml`](../../.github/workflows/ci.yml); this page explains how to interpret
their evidence and what remains a human action.

**Schedule activation remains PAUSED.** A green CI run proves only the approved
current source-scope checks for its exact commit. It does not claim full-source
conformance, personal release readiness, or shadow graduation. Missing personal
evidence stays `PENDING`; synthetic fixtures cannot satisfy personal-day gates.

## CI release gates

CI runs on pushes to `main` and pull requests targeting `main`. The `verify`
job runs once per Python 3.12, 3.13, and 3.14. It installs hash-locked
requirements, installs this project without resolving a second dependency set,
and checks that exporting the lock reproduces `requirements.lock`. GitHub grants
the job `contents: read` only. CI uses temporary private data/configuration
roots and clears the Alpaca and OpenAI credential variables.

The receipt collector accepts a check only when the exact requested source
commit has one completed `verify` matrix job for every declared Python version
and each required named step appears once with a completed conclusion. The
closed release check ids map to these workflow steps:

| Release check id | Exact CI step name |
| --- | --- |
| `FORMAT` | `Check formatting` |
| `LINT` | `Check Ruff` |
| `TYPES` | `Check mypy` |
| `UNIT_AND_INTEGRATION_TESTS` | `Check unit and integration tests` |
| `SCENARIO_AND_FAILURE_MATRIX` | `Check scenarios and failure matrix` |
| `BRANCH_COVERAGE` | `Check whole-package coverage` |
| `SCHEMA_DRIFT` | `Check generated schemas` |
| `DOCS_AND_EXAMPLES` | `Check docs and examples` |
| `SECRET_SCAN` | `Check tracked secrets` |
| `NETWORK_BOUNDARY` | `Check offline network boundary` |
| `PLUGIN_WHEEL_SDIST` | `Check plugin wheel and sdist` |
| `FRESH_WHEEL_INSTALL` | `Check fresh wheel installation` |
| `DOCTOR` | `Check local doctor` |
| `COMPATIBILITY` | `Check compatibility consumers` |

The coverage step enforces at least 90% line coverage across the package. The
nonlive test partitions set `FINANCE_RESEARCH_AGENT_OFFLINE_TESTS=1` and install
the network guard. They block outbound network access while retaining local
stdio/Unix-socket process communication. Plugin artifacts are tested from built
wheel and source distributions; the fresh-wheel test imports from outside the
checkout so source-tree imports cannot hide a packaging gap.

The tracked secret check scans paths returned by `git ls-files`; it does not
upload private evaluation data. Its baseline must be manually audited and must
not contain a live credential. The workflow and exact step names are covered by
the offline release-boundary tests.

## Local doctor

Run `bash scripts/doctor.sh` from the checkout with Python 3.12 or newer and an
existing writable private data root. Set `AI_MARKET_RESEARCH_DATA_DIR` when the
root is not the configured default. The shell checks the root, creates a
temporary write probe, removes it on exit, then runs the doctor.

The local doctor checks project/lock/installed version agreement, private data
permissions, configuration and host policy, generated schemas, documentation
and skill contracts, plugin metadata, and bounded MCP initialize/list-tools
contracts. It reports whether Alpaca credentials are configured without
printing their values. **The local doctor does not call a provider.** It does
not prepare or publish research, change policies, create a permanent run, or
start scheduling. Its local plugin/MCP contract checks run under the offline
network guard.

Useful read-only local checks include:

```bash
python -m finance_research_agent.schema_export --check
python -m scripts.check_docs_examples
python -m scripts.check_secrets
ruff check .
mypy src
```

The workflow remains the release authority for the full test partitions, whole
package coverage, locked interpreter matrix, and fresh wheel installation.

## Exact-head GitHub evidence

After the target commit has a completed CI run, authenticate `gh` with read
access to this repository's Actions metadata and collect the evidence from the
checkout containing that exact commit:

```bash
gh auth status
python -m scripts.collect_release_evidence --source-commit "$(git rev-parse HEAD)"
```

The collector makes read-only GitHub API requests for the configured
`BenSun08/finance-research-agent` repository and `ci.yml` workflow. `PASS`
means all 14 named checks were found for all three Python versions on the exact
commit. A failed exact-head step produces `FAIL`.
**Missing exact-head CI evidence is PENDING.** Missing authentication/network
access, an incomplete run, or a missing exact-head step also stays `PENDING`;
do not reuse a receipt from an older commit.

Receipt batches, the current status pointer, and the interprocess collection
lock are written only below
`$AI_MARKET_RESEARCH_DATA_DIR/diagnostics/evaluation`, in private directories
with restricted file permissions. Collections are serialized so a slower
older refresh cannot replace a later pending result. The lock retains pending
markers for exact source commits when a refresh cannot update the status
pointer, so readers cannot fall back to an older PASS batch. A completed
collection clears only its own marker after storing its status. The evaluation
evidence reader accepts only the selected batch bound to the configured source
commit, tree, repository, and workflow. No credential or run artifact is
stored.

## Operator evidence and live smoke

The observation and attestation entrypoints each accept one strict JSON request
on standard input and emit a service-stamped receipt on standard output:

```bash
python -m scripts.record_shadow_observation < shadow-day.json
python -m scripts.record_operator_release_attestation < operator-check.json
```

They have no data-root/path override. Configure an existing mode-0700 data root;
records are append-only beneath `diagnostics/evaluation/shadow-observations`
and `diagnostics/evaluation/operator-attestations`. The shadow-day writer
checks XNYS dates and references to local run/feedback records. Neither command
calls a market-data provider, prepares or publishes research, or schedules work.
Record an attestation only for a check that a person actually performed. CI, a
local doctor result, or a successful import is not a substitute for an operator
observation.

Manual attestation ids are `DESKTOP_INSTALL`, `HOST_RESTART`,
`SKILL_DISCOVERY_TRACE`, `SUPERVISED_SYNTHETIC_WORKFLOW`, and
`ALPACA_LIVE_SMOKE`. Each record is a self-attributed operator statement; the
writer stores it but does not execute or verify the claimed action. Unrecorded
actions remain `PENDING` in readiness evidence.

The real Alpaca smoke is separate and opt-in:

```bash
python -m pytest -m live tests/live/test_alpaca_smoke.py
```

It requires explicit Alpaca credentials, an XNYS trading day, and the 04:00 to
09:30 Eastern premarket window. It resolves the last two completed calendar
sessions at runtime, validates the fixed market-data-only source policy before
constructing the HTTP client, and reads IEX data for `SPY`. Default CI excludes
live tests and this migration does not run the smoke. Its personal operator
result remains `PENDING` until an authorized operator opts in and records the
actual outcome. No current R11 result activates scheduling or authorizes a
trade.

## Compatibility consumers retained

The compatibility inventory combines an AST `ImportFrom` scan over `src`,
`tests`, `examples`, and Python files under `docs` with targeted `rg` searches
over those paths, `README.md`, and each module's declared exports. There is no
tracked `examples/` tree. The inventory shows these supported consumers:

| API or export | Evidence from source, tests, docs, and exports | Decision |
| --- | --- | --- |
| `domain.market.MarketSnapshot` legacy alias to `RegimeMarketSnapshot` | Exported by the historical domain module; used by the historical-bars bridge and characterization, bridge, regime-workflow, synthetic-fixture, and regime tests. The characterization test records the import path explicitly. | Retain; keep distinct from canonical `domain.models.MarketSnapshot`. |
| `domain.models.MarketSnapshot` canonical Product A model | Used by collection/market bridges, packet and prior-research services, domain eligibility/setup code, schema export, and evaluation assertions; covered by application, adapter, contract, and unit tests. | Retain as the current typed market model. |
| `market_data.to_market_snapshot` | Exported from `market_data` and `historical`; called by adapter and unit tests, listed in the characterization surface, and documented in the README. `application/regime_workflow.py` has its own standalone projection function with the same name. | Retain the exported historical projection and both workflow bridges. |
| `application.HistoricalBarsFetcher` and `application.run_regime_research` | Exported from `application`; the research service consumes the port. Application parity and characterization tests exercise the fetcher/client seam; README documents the port. | Retain the provider-neutral port and standalone historical workflow. |
| `adapters.alpaca_historical.AlpacaHistoricalBarsClient` | Declared in the adapter's `__all__`; used by application parity and characterization tests and its dedicated adapter test module. | Retain the market-data-only historical client. |
| `agent.AgentRuntime` | Exported by `agent`; directly exercised by `tests/agent/test_runtime.py` and documented throughout the README. No Product A service depends on it. | Retain the explicitly supported generic runtime foundation. |
| `evals.evaluate_regime_case(s)`, `evals.run_regime_benchmark`, replay APIs | Exported by `evals`; used by the standalone benchmark and walk-forward modules, their evaluation tests, and README examples/reference sections. | Retain the standalone deterministic evaluation APIs. |

No API removal is supported by this evidence. Internal test references are
reported separately from application consumers; the characterization suite
also protects the declared public import surface.
