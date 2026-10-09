# Product A Preparation Handoff Correction

Status: Selected under the user's standing delegated decision authority.

## Reason and scope

PRs #62–65 expose a typed operation named `prepare_premarket_run`, but its
implementation only initializes `CONFIG_FROZEN`. Product A spec §9.1 and the
approved workflow require a bounded frozen packet or a self-published operational
failure. Resolve this application prerequisite before publishing workflow skills.
This corrects R10's composition contract; it does not add a provider or a workflow
engine.

## Decisions

- Preserve the existing run initializer API for compatibility. Add a separate
  application preparation orchestrator and route the typed operation through it.
- Return the window decision and stored run, plus exactly the applicable frozen
  packet or operational publication. A non-runnable window has neither. An
  explicitly requested published revision returns its immutable publication.
- Freeze versions from trusted composition inputs. The prompt version is the
  digest of the canonical prompt bytes; policy versions must still match the
  frozen configuration. A resumed revision retains its original versions.
- Reuse collection, evidence freeze, quality checkpoint, eligibility, regime,
  event assessment, setup assessment, packet assembly, and publication services.
  Add only the missing projection from collected canonical snapshots to regime
  inputs. Preserve evidence identifiers and Decimal values.
- Keep Alpaca market data and the local market calendar as the only sources.
  Missing macro/event verification remains an explicit blocking gate. Do not
  synthesize healthy event sources. The current source scope therefore produces
  watchlist exclusions, without actionable candidates or plans.
- A packet must retain the deterministic regime result, including unavailable
  component reasons, as well as its metrics. Existing packet fixtures remain
  readable through an optional additive field; new preparation always fills it.
- Restore staged packet bytes and checkpoint hashes on resume; never refresh
  evidence after its cutoff. Do not silently accept staged corruption or replace
  a staged packet after component changes.
- Use the existing 15 minute normal-provider duration target as the application
  deadline. Check it between bounded stages and cap every actual provider request
  by the same trusted deadline. No deadline is accepted from MCP arguments.
- Deadline-bound DNS admission uses at most one unfinished resolver worker per
  client. The caller stops waiting at its remaining deadline and sends no HTTP
  request afterward. The operating system DNS call cannot be cancelled and its
  daemon worker may finish later; repeated calls do not create additional workers.
  Core transport timeout/network/protocol errors retain bounded retry semantics
  and become closed provider failures when exhausted.
- Use a trusted default packet limit of 8,000,000 bytes, overridable only at
  application composition for offline tests/deployment. Protected overflow fails
  closed and publishes an operational report.
  The initial 5,000,000-byte selection was insufficient: the complete example
  configuration with 23 symbols and 252 completed sessions is 5,208,120 bytes.
- The publication envelope uses a separate bounded artifact JSON value type
  permitting arrays up to 8,192 entries. This stores full 252-session snapshots
  and packet collections without weakening the 128-entry limit on evidence and
  model-facing `JsonValue` fields. Existing publication bytes are unchanged.
- Pre-synthesis hard failures after a run is allocated publish only a closed
  failure code. Publication/storage failures propagate to the existing redacted
  transport boundary; no successful publication is inferred.
- Prior observations require immutable prior publication discovery. Add a bounded
  repository query and freeze selected prior plans before collection; preserve
  completed-bar observation semantics and disclose unavailable observations.
- Provide a launchable stdio composition root after preparation is complete. Use
  the validated frozen source policy for real provider construction, never the
  diagnostic CLI's readiness stub. Startup must not require valid credentials or
  write storage merely to initialize/list tools.

## Delivery and verification

Deliver the correction in reviewed features with tested commits: version/projection prerequisites;
packet/typed-result contract; preparation/resume/failure/deadline composition;
prior observations; runnable stdio bootstrap. Each commit records focused
RED/GREEN and checks. Before each feature PR, independently review that whole
feature, analyze line/branch coverage, run full pytest/Ruff/mypy and schema
drift checks, then check exact-head CI/reviews before merging.

R10 Task 5/6 packaging sequencing must be reconciled explicitly when reached:
Task 5 cannot compute an installed premarket bundle digest before its canonical
skill resources exist. Do not publish an apparently runnable placeholder skill.
R11's scenario/scorecard/readiness work follows R10 and retains the current
source scope and the separate shadow-graduation gate.
