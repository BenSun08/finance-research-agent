# Product A Preparation Handoff Evidence

Base: `0519c8126cde4e15343821877a7c87837ea25fa9` (merged PR #65).
Design: `../specs/2026-10-08-product-a-preparation-handoff-correction.md`.

## Subtask 1: Trusted frozen component versions

- RED: preparation tests failed for the placeholder prompt digest and missing
  trusted version callback; the next run exposed the 64-character version bound
  rejecting the approved 71-character skill digest.
- GREEN: 31 focused preparation/dependencies/packet tests passed. Malformed skill
  digests fail before allocation; a resumed revision does not consult new versions.
- Full suite: 1,915 passed, one live test deselected, one existing websockets warning.
- Ruff passed; mypy passed (87 source files); final diff check passed.
- Schema regeneration initially used the shared virtualenv's older installed
  checkout and caused the schema drift test to fail. Regenerated with explicit
  `PYTHONPATH=src`; full suite then passed. Only the five schema exports containing
  the changed skill version field remain changed.
- Whole-feature independent review and coverage remain pending. This subtask
  does not claim an operational prepare handoff.

## Subtask 2: Collected snapshot to regime projection

- RED: four tests failed because the collection-compatible projection was absent.
- GREEN: 24 collection/historical bridge tests passed, including numeric/evidence
  fidelity, changed-content identity, and empty/late/inconsistent input rejection.
- Full suite: 1,919 passed, one live test deselected, one existing warning.
- Ruff passed after test wrapping; mypy passed (87 source files). The legacy
  historical bridge remains unchanged.

## Subtask 3: Retain the typed regime result

- RED: three packet tests failed because the builder could not accept a regime
  result. Test setup was corrected to use the frozen configuration's version
  before accepting those failures as RED evidence.
- GREEN: 14 packet tests passed, including complete UNKNOWN/unavailable state,
  wrong-policy/late-result rejection, and legacy byte round trips.
- Full suite: 1,923 passed, one live test deselected, one existing warning.
- Ruff and mypy passed; schema check passed. The absent optional field is excluded
  from serialization to preserve older packet identity and byte counts.

## Prerequisite feature review and coverage

- Independent review of the whole prerequisite scope: review-clean. Reviewer ran
  85 preparation/bridge/packet/replay tests plus populated UNKNOWN and permissive
  regime probes, and compared legacy packet bytes against base `0519c81`.
- Persisted the review's regression recommendations: a base-generated golden hash
  and byte count; known Decimal regime score and 38 metrics; missing/replaced
  metric rejection; late source/bar cutoff; naive/non-UTC cutoff; cutoff identity.
  Reviewer rechecked the test-only diff and independently passed 40 tests.
- Final full suite under branch coverage: 1,931 passed, one live test deselected,
  one existing warning. Ruff/mypy/schema drift/diff checks passed.
- Coverage source is the worktree directory, not pre-imported module names. The
  discarded module-name measurement caused enum/class import interference; its
  failures and coverage were not accepted as verification evidence.

| Module | Statement coverage | Branch coverage |
| --- | ---: | ---: |
| component_versions | 100% (7/7) | No branches |
| run_service | 95.24% (80/84) | 86.67% (26/30) |
| collection_bridge | 92.52% (136/147) | 83.33% (55/66) |
| packet_service | 91.11% (82/90) | 76.47% (26/34) |
| domain/packets | 78.65% (70/89) | 50% (22/44) |

The added regime validation and collected-snapshot projection rejection cases
are covered. Remaining missing branches are existing collection defensive checks,
run reload/window guards, packet trimming paths, and general packet cutoff/size
validation. These figures do not claim 100% coverage or operational preparation.

This feature contains prerequisites only. The typed prepare handoff, shared
deadline, prior observations, stdio executable, plugin/skills and R11 remain.

## Preparation handoff feature (base: merged PR #66, `6f08022`)

### Tested subtasks

- `664fa39`: trusted request/run deadline, closed Alpaca deadline failures.
  Initial RED: 12 failures; GREEN: 16 focused cases. Review found that body reads
  retained the original timeout after headers. A realistic HTTPX/httpcore RED
  probe observed timeouts `[1.0, 1.0]` and rejection at 1.6 seconds; GREEN uses
  `[1.0, 0.2]` and rejects at 1.0 second. A separate expired-iterator RED now
  makes zero iterator advances. Final scoped adapter/security tests: 97 passed.
- `dc8acf9`: strict JSON model-valued FrozenMap reload. The generic model-map
  round-trip and actual populated-packet resume failed before the schema fix.
  JSON preserves nested model validation mode; complete otherwise-valid Python
  payloads still reject string integers, Decimal strings, and timestamp strings.
- `f351ae9`: full-history evidence projection. RED: the 252-bar array exceeded
  the 128-value evidence bound. GREEN: deterministic 128/124-bar chunks retain
  every bar and original evidence ID; short-history payload identity is retained.
  Combined deadline/foundation/bridge verification: 119 tests passed.
- `1287144`: separate bounded artifact JSON envelope. Actual full preparation
  publication failed with 263 array-bound errors. GREEN: the 5,208,120-byte
  example packet publishes and reloads exactly. Artifact arrays permit 8,192
  entries; evidence arrays remain bounded at 128, maps at 128, strings at 8,192.
  Foundation tests: 41 passed, including bounds, strictness, detached immutable
  inputs, and unchanged bytes for previously valid values.
- `fc860e5`: prior-publication discovery and post-freeze failure storage.
  Initial query RED: 21 missing-method failures; later integrity RED: three
  inconsistent-state failures and one invalid date-directory shape. Review
  regressions reject unrecognized packetless origin/code/quality, malformed
  telemetry pairs/schema/hash/checkpoint, missing or inconsistent legacy final
  runs, and missing/corrupt/symlinked final telemetry artifacts. No fallback to
  older research occurs on corruption. Nine failure-checkpoint tests preserve
  cutoff, identity, execution/delivery states, hashes, closed reason, and no-packet
  restriction. Combined query/prior/failure verification: 160 tests passed.
- `53645e1`: immutable prior selection and completed-bar observations.
  Initial missing-module RED preceded implementation. Review RED reproduced
  crash-before-checkpoint recovery and selection after collection (four cases),
  then actual synthesized publication origin failed because it is stored inside
  the draft. GREEN restores canonical staged bytes without publication requery,
  preserves CAS semantics, prohibits late first selection, accepts real validated
  publication shape, and skips blocked reduced publications. Prior reference
  evidence is retained; missing observation inputs produce a closed gate.
  Scoped coverage: 111/111 statements and 48/48 branches. Query coverage:
  98/98 statements and 64/64 branches. These are scoped coverage measurements.

### Orchestration and review regressions

- Initial preparation RED: six tests could not import the missing orchestrator.
  Public-operation RED proved the facade still returned CONFIG_FROZEN.
- New preparation composes existing collection, freeze, quality, regime,
  eligibility/event/setup assessment, packet/reduced staging and publication.
  Missing macro health remains a blocking gate; current source scope has no
  candidates or conditional plans. No healthy event source is fabricated.
- Review corrections use the prior trading session for Monday observations and
  named packet-builder arguments. An actual configured prior symbol now produces
  a completed-bar observation with its retained reference evidence.
- Review RED reproduced a budget-failure reason staged before checkpoint crash,
  followed by a late resume; the original INTERNAL_ERROR is now recovered.
- Known historical-calendar failure RED escaped after allocation. The typed
  RuntimeError subclass now maps only that known failure to an operational
  MARKET_CALENDAR_UNAVAILABLE; storage/publication errors still propagate.
- Scheduled explicit-revision requests are rejected before the published shortcut.
  Typed provider DEADLINE_EXCEEDED produces an operational result even when the
  test clock does not advance. Frozen-quality resume RED attempted current
  provider reconstruction; GREEN reuses frozen quality without that factory.
- First full integration/coverage baseline: 2,093 passed, one live test deselected,
  one existing websockets warning, 356.08 seconds. Ruff, mypy (89 source files),
  schema drift and diff checks passed. This baseline predates the final DNS and
  frozen-quality-resume additions; final verification remains below.

### Final verification and review

- `94ebc64`: bounded DNS admission and closed core transport failures. Three
  wall-clock RED cases showed a one-second resolver escaping a 50 ms deadline
  and repeated-call worker growth; all three passed after the correction.
  Core transport RED included 17 exception cases and four additional protocol
  cases. Final adapter/deadline/security verification passed 190 tests. DNS is
  bounded at the caller and admits at most one pending worker per client; the
  underlying OS resolution itself cannot be cancelled and may finish later.
- `15d28bb`: typed preparation handoff and public facade. Fresh review regressions
  cover the second calendar lookup after allocation, stored publication context
  against immutable bundle metadata, original scheduled-window preservation on
  manual resume, and frozen-quality resume without current provider construction.
- Independent whole-feature reviewer `review_handoff_final` approved integration
  with no remaining actionable P1/P2 findings. It inspected the complete feature
  against `6f08022`, ran 131 tests initially and 40 targeted tests after fixes,
  and verified the deadline/DNS corrections. Its verdict is separate from CI.
- Frozen-source full suite: **2,131 passed, one live test deselected, one existing
  websockets deprecation warning**, 143.08 seconds. Final HTTP selection: 98 passed.
  Ruff passed; mypy passed across 89 source files; schema drift and diff checks
  passed. Earlier overlapping runs imported the pre-fix HTTP protocol handler and
  had four failures; those runs are discarded as final verification evidence.

| Scope | Statement coverage | Branch coverage |
| --- | ---: | ---: |
| Preparation orchestrator | 88.30% (166/188) | 72.86% (51/70) |
| HTTP client, focused adapter selection | 80.00% (396/495) | 58.75% (94/160) |
| Prior research helper, scoped | 100% (111/111) | 100% (48/48) |
| Prior publication query, scoped | 100% (98/98) | 100% (64/64) |

Preparation gaps include invalid result construction, missing verified artifacts,
hash/canonical-byte corruption guards, missing-symbol exclusions and the guard
against plan-producing setups. HTTP figures include existing parsing/security
branches outside the focused selection. These figures do not claim complete
coverage. Stale HTTP coverage from the overlapping run was explicitly removed
and replaced with a fresh 98-test frozen-source measurement; the preparation
measurement retained only unchanged source whose selected cases passed.

The executable stdio bootstrap, R10 plugin/skills/workflow and R11 remain
subsequent features. PR state, exact-head CI and merge are independently verified
at publication time; this document does not predeclare those remote results.

## Executable stdio bootstrap (base: merged PR #68, `92fe327`)

### Subtask 1: Lazy trusted composition

- RED: bootstrap test collection failed because the executable composition module
  did not exist. Tests covered deferred construction, validation before dispatch,
  failure recovery, complete market-data forwarding, frozen policy/run deadline
  isolation, and production retry sleep before implementation.
- Additional RED: three stored/watchlist operations failed on eager calendar
  construction and the lazy-calendar forwarding test failed on its missing type.
  After deferring calendar construction, the isolated expired-client test failed
  because the adapter deadline exception was not an application TimeoutError.
- GREEN: 36 bootstrap/MCP/application-facade tests passed. Calendar and settings
  factories cache successful construction only; actual run providers are distinct
  and receive their frozen source policy and exact trusted deadline. Constructor
  expiry maps to a closed application timeout with suppressed exception context.
- Scoped Ruff and mypy checks passed; final diff check passed. HTTP sessions,
  streams and transports retain their existing request-local cleanup ownership.
- Real stdio subprocess, preparation integration, packaging, whole-feature review,
  full-suite coverage and repository checks remain pending at this subtask.

### Subtask 2: Real-provider symbol-order handoff correction

- Real offline bootstrap preparation exposed an existing adapter mismatch: the
  watchlist-first collection order was passed to HistoricalDailyBarsRequest,
  which requires canonical sorted symbols. Healthy daily-bar responses became
  INVALID_REQUEST and preparation published an operational failure.
- RED: the focused unsorted `(MSFT, AAPL)` regression returned ProviderFailure
  for AAPL instead of its normalized bars. The whole real-provider packet and
  frozen-quality resume cases also failed before this correction.
- GREEN: sort only the normalizer request's symbol declaration. The provider's
  HTTP query retains caller order; returned bars/failures remain correctly bound
  to symbols, identities, session dates, feed, adjustment and evidence cutoff.
- 197 Alpaca/historical/collection/security tests passed, with one existing
  websockets deprecation warning. Scoped Ruff/mypy and diff checks passed.
- This narrow correction was explicitly authorized after the integration probe;
  it adds no provider or financial capability.

### Subtask 3: Executable entrypoint and complete offline handoff

- RED: console metadata lacked the authoritative ai-market-research-mcp entry.
  Added that entry using the current finance_research_agent package namespace;
  the existing diagnostic CLI remains separate. README documents launch, the
  environment-only data locator, deferred credential access and current scope.
- GREEN: 173 focused bootstrap/MCP/facade/preparation/deadline/settings/schema/CLI
  tests passed. The selection includes real subprocess initialize/list against
  a malformed owner-readable .env secret canary, with empty captured output and
  no data directory or file-content changes.
- Actual Alpaca adapter composition over offline HTTP produces the frozen packet
  with injected component versions and real regime results. Missing credentials
  and transport failures self-publish closed operational outcomes. Packet,
  quality-checkpoint and published resumes work after removing current config
  and forbidding settings, client and version factories.
- Success closes all three request streams/transports; network failures close
  their request clients; a body reaching the run cap closes its stream/transport
  and publishes DEADLINE_EXCEEDED without another network request. Real provider
  readiness/status remains read-only and sends no requests.
- Offline wheel build passed using the bundled Python 3.12/setuptools runtime.
  A temporary --no-deps/--no-index installation supplied the generated console
  script, verified from an unrelated directory with only its installed site on
  PYTHONPATH. Package imports had no source fallback; the packaged canonical
  prompt digest matched root prompt bytes; installed initialize/list returned
  exactly eleven tools despite malformed .env and made no storage changes.
  Temporary build/install artifacts were cleaned without changing the active
  environment or installing dependencies.
- Scoped Ruff, mypy (two changed source files), schema drift and diff checks
  passed. Independent whole-feature review, full repository checks and coverage
  remain the coordinator's next gate. No push, PR or CI result is claimed here.
