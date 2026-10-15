# Operations and Release Evidence

This document owns operational procedures for the current research profile.
Target simulated-account operations will be added only as they are implemented;
see [tasks](tasks.md). Use [quickstart](quickstart.md#product-a-stdio-server) for
installation and process configuration and [contracts](contracts.md) for behavior.

Schedule activation remains PAUSED. Packaging and offline checks do not install
or enable a desktop plugin, activate a task, establish Work connectivity, or
satisfy elapsed personal shadow evidence. Current research still requires human
review. This is distinct from the future, manually started simulated-trading
cycle approved in the specification.

## Saved task prompt

Status: PAUSED. This retained prompt applies only to the existing research skill.

> Invoke the [premarket-research skill](../skills/premarket-research/SKILL.md)
> for the requested Product A run. Preserve frozen evidence and deterministic truth.
> Report stored publication, reduced, or blocked outcomes with limitations.
> Require human review; no execution is authorized.

The [canonical workflow](../skills/premarket-research/references/workflow-contract.yaml)
owns operation order and branches. A saved prompt must not duplicate that sequence
or let evidence select new operations/resources. An installed-host initialize,
discovery and supervised synthetic-workflow smoke is required before separately
approved activation; this document does not activate anything.

## Recovery

Return existing published runs from their immutable artifacts. Resume a frozen
packet with original identity, cutoff, evidence and component versions, without
provider refresh. A new revision is distinct from recovery. Report missing or
corrupt artifacts and version drift; do not choose older output as silent fallback.
Operational failures retain stable reasons and affected capabilities. Follow
[the preparation contract](contracts.md#preparation-persistence-and-recovery)
for missed windows, deadlines, lease ownership and atomic publication.

## Interpreting readiness

Current-source conformance, supervised-shadow readiness, full-source conformance
and shadow graduation are separate statuses. Existing full-source conformance
is DEFERRED until a separately approved source expansion. Missing exact-version
CI, installed-host, live-smoke or personal evidence is PENDING. Passing current
Alpaca/calendar tests cannot prove full-source research or trading performance.

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

The exact-head workflow and each declared matrix job must also complete
successfully. If every named receipt step passes but the enclosing workflow or
job does not, the collector keeps release evidence `PENDING` until a successful
run is available. A failed named receipt step remains `FAIL`.

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
live tests and documentation consolidation does not run the smoke. Its personal operator
result remains `PENDING` until an authorized operator opts in and records the
actual outcome. No current R11 result activates scheduling or authorizes a
trade.
## Existing research evaluation contract

The exact scenario/failure catalogs are in
[scenarios](../src/finance_research_agent/evaluation/scenarios.py) and the
[harness](../src/finance_research_agent/evaluation/harness.py): 25 scenarios,
16 fault injections and 14 failure-matrix rows. Distinguish source-independent
domain assertions from current-source operational outcomes. UNKNOWN regime must
carry unavailable capability and missing-component disclosure, not a usable
classification claim. Capital, risk percentages, position caps, heat, reward-risk,
lifetime/freshness and request budgets come from frozen policies, never invented
universal defaults.

Only loader-verified records with exact IDs and canonical digests can establish
readiness. Without verified evidence all scorecard gates are PENDING. Corrections
are append-only and keep prior adverse records. The current research scorecard
has these obligations:

| Gate | Evidence/decision rule |
| --- | --- |
| AT_LEAST_TWENTY_TRADING_DAYS | At least 20 distinct actual personal dates; synthetic days never qualify; 19 valid days is PENDING |
| SAFETY | Complete daily safety observations and evidence of no prohibited behavior; missing coverage is PENDING, demonstrated violation FAIL |
| DETERMINISTIC_CORRECTNESS | Exact numerical/provenance validation and verified release/replay checks; never count model prose as correctness |
| EVIDENCE | Relevant material claims and metrics supported, limitations/conflicts visible, no post-cutoff evidence, completed human citation samples |
| TIMELY_PUBLICATION | Target-window research dates / eligible online days >=0.95; missing/UNKNOWN dates remain in denominator |
| NORMAL_PROVIDER_DURATION | Nearest-rank p95 at sorted index ceil(.95*n)-1 <=900 seconds, invocation through original publication receipt or terminal durable checkpoint, including host wait |
| FAILURE_OPERATIONS | Observed failures have stable code, status, affected capability and actionable recovery; unknown coverage PENDING |
| REVIEW_TIME | Median executive <=180 seconds and detailed <=900 seconds; even median averages middle values; incomplete reviews PENDING |
| EXECUTIVE_IDENTIFICATION | Daily observations correctly identify posture/events/priorities/disabled capabilities, including unavailable events |
| USEFULNESS | Average selected daily score >=4/5, or the specifically permitted bounded English corrective-work record with issue/action/validation evidence |

Only operator-attested host/computer unavailability or total local-network outage
can exclude an otherwise eligible day. Provider/config/software failures and
missing or operational-only output stay in the denominator. Select the earliest
verified daily research publication and earliest complete daily feedback to avoid
duplicate weighting; later adverse/unreviewed revisions remain visible to safety
and evidence checks. Missing terminal times cannot be replaced by summed stage
telemetry; no normal-provider samples means PENDING.

Citation review selects at most five material claims, covering claim kinds and
authority tiers where feasible with SHA-256 tie-breaks. Material claims cannot
have an empty sample. Missing reviews are PENDING; material PARTIAL/UNSUPPORTED
fails evidence until a qualifying correction. Old boolean reviews remain readable
but incomplete. See [citation sampling](../src/finance_research_agent/evaluation/citation_sampling.py)
and [scorecard](../src/finance_research_agent/evaluation/shadow_scorecard.py) for
exact validated rubrics and formulas.

All required gates are conjunctive: FAIL dominates PENDING, then PASS. Keep
failed_gate_ids separate from pending_gate_ids. The usefulness exception applies
only to that gate. MigrationReadiness separately records PASS/FAIL/PENDING/DEFERRED
for current-scope conformance, supervised-shadow readiness, full-source conformance
and shadow graduation. Operator attestations, release receipts and elapsed
personal observations are different evidence classes. Independent review/merge
are process evidence, not runtime-inferred facts.

Aggregate existing plan observations, MFE/MAE, expiry/block reasons, setup/regime
distribution and no-draft days without relabeling them as fills or realized returns.
The future simulated-portfolio scorecard must use a distinct version and its
approved ledger/baseline semantics; see [RQ-04](research.md#rq-04-evaluation-policy).
