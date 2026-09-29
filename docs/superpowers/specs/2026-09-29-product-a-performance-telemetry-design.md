# Product A Run-Scoped Performance Telemetry Design

**Status:** Selected design for the remaining R9 telemetry item.

**Authority:** The approved Product A specification, especially sections 37.4
and 38; the 2026-09-16 conformance migration plan, R9; and the approved source
scope, which currently requires only `market-data` and `market-calendar`.

## Purpose

Product A already defines an immutable `PerformanceTelemetry` value, but no
application service captures or persists it. This design completes that R9
boundary with run-scoped measurements that survive staged recovery and remain
available in immutable published artifacts.

The implementation adds no provider, model, scheduler, MCP operation, cache, or
brokerage capability. It does not treat telemetry as financial evidence or
change deterministic domain decisions.

## Selected approach

Use one run-scoped recorder shared by application stages and the existing safe
HTTP boundary. Application code starts a named stage measurement; the HTTP
boundary reports only the adapter identifier, physical request-attempt count,
and response-body byte count to the active recorder. The recorder aggregates
bounded counters and emits an immutable `PerformanceTelemetry` snapshot.

Persist each completed snapshot as a content-addressed staged artifact and bind
its digest to the corresponding `RunCheckpoint`. Publication copies the same
snapshot into the published bundle. Replay validates and returns the frozen
snapshot without contacting providers or recalculating current telemetry.

This approach measures the actual approved external request boundary while
avoiding changes to the `MarketDataProvider` call signatures. It also makes the
local calendar's status explicit: `market-calendar` has zero external requests
and zero response bytes, while its stage time remains part of run timing.

### Alternatives considered

1. **Count calls at the application provider port.** This avoids HTTP-layer
   work but undercounts retries, redirects, and paginated requests, and it
   cannot report response-body sizes. It does not satisfy the provider request
   and response measurements in section 38.
2. **Measure at the safe transport boundary (selected).** This observes each
   bounded HTTP attempt and every accepted response body without changing
   provider method signatures. The run-scoped observer must be explicit and
   secret-free; snapshots are persisted with checkpoints.
3. **Accept a completed telemetry value from the caller.** This makes storage
   easy, but gives the application no trusted way to measure values and permits
   inconsistent or fabricated measurements to enter a published artifact.

## Measurement contract

All counters are run-scoped and cumulative across successfully checkpointed
stages. A failed or interrupted stage records its measurements when its failure
checkpoint is written. An abrupt process loss before a checkpoint may omit
measurements from that uncheckpointed stage; earlier checkpointed measurements
remain intact.

- `provider_request_counts` counts physical HTTP request attempts for the
  approved Alpaca market-data adapter. Redirect and retry requests each count
  as an attempt. A connection attempt that fails before a response still
  counts as an attempted request.
- `response_bytes_by_provider` counts response body bytes delivered to adapter
  code in a completed `SafeResponse`. It is not a claim about TLS, compressed,
  or otherwise unconsumed wire bytes. A failed connection contributes zero
  bytes.
- The provider maps contain only configured source providers. `alpaca` is
  measured at the safe HTTP boundary. The local `market-calendar` adapter has
  zero requests and zero response bytes.
- `stage_durations_ms` accumulates elapsed monotonic time for the fixed R9
  stages: `RUN_PREPARATION`, `MARKET_COLLECTION`, `QUALITY_EVALUATION`,
  `PACKET_ASSEMBLY`, `VALIDATION`, and `PUBLICATION`. Sub-millisecond values
  round down to zero. Wall-clock timestamps continue to govern evidence
  cutoffs; monotonic time is used only for duration measurement.
- `synthesis_attempts` is zero while production model integration remains
  prohibited. Caller-supplied drafts are not counted as model attempts.
- `validation_attempts` is derived from persisted `VALIDATING` checkpoints and
  remains bounded to the initial attempt plus two repairs.
- `research_packet_bytes` is the byte length of the exact canonical staged
  packet. It remains zero until that artifact exists.
- `cache_hits` and `cache_misses` are zero because R9 has no run-scoped market
  data response cache. The adapter's instrument-identity memo is not a market
  data cache and is excluded.
- `deadline_budget_ms` records the 900,000 ms (15 minute) normal-provider
  duration target in Product A specification §37.4. It is a performance target,
  not a hard execution deadline. `deadline_consumed_ms` reports actual
  accumulated stage time and may exceed the target. `remaining_budget_ms` is
  `max(0, target - consumed)`. A run that exceeds the target continues under
  existing source request deadlines and failure behavior; telemetry does not
  cancel work or change publication eligibility. This avoids converting a p95
  service target into a new per-run timeout policy.
- `response_bytes_total`, `cache_hit_ratio`, and remaining-budget values are
  derived and validated against their source counters.

## Data flow and boundaries

1. A run-scoped recorder loads the latest checkpointed telemetry snapshot, or
   initializes the empty snapshot when the run has no telemetry yet.
2. Each application service measures its named stage with an injected monotonic
   clock and adds only aggregate counters to the recorder.
3. `SafeHttpClient` reports attempts and consumed response-body byte counts for
   the active run. Its observer receives no URL, query, headers, body content,
   credential, raw exception, or provider message.
4. When a stage writes its checkpoint, the recorder stages the canonical
   telemetry bytes and includes their digest in the checkpoint's artifact
   hashes. This binds the measurements to the same compare-and-swap checkpoint
   transition as the stage result.
5. Normal, reduced, and operational-failure publications include the latest
   verified telemetry value in the immutable bundle. A failed atomic rename can
   retry only with the same frozen value.
6. Artifact replay verifies the checkpoint/artifact hashes and returns the
   recorded telemetry. It does not invoke clocks, providers, or mutable current
   configuration to regenerate measurements.

## Failure and security behavior

- Measurement is best-effort only with respect to unavailable timing detail;
  it never changes a quality result, provider failure code, packet, or report
  decision.
- A malformed telemetry artifact, hash mismatch, impossible counter total, or
  snapshot mismatch fails closed through the existing checkpoint/publication
  integrity boundary.
- Raw provider errors are not telemetry fields. Existing closed `ErrorCode`
  values and operational reports remain the failure record.
- Telemetry contains bounded provider/stage identifiers and numeric aggregates
  only. No URL, symbol, source excerpt, prompt, report prose, configuration
  path, secret, or authorization value is recorded.
- Only the configured `market-data` and `market-calendar` roles participate.
  SEC, macro, news, company-IR, model, and other integrations are out of scope.

## Verification

Tests prove exact aggregate values from hand-checked fixtures, retry and
redirect request counting without response-content leakage, stage timing with a
deterministic monotonic clock, over-target accounting without cancellation, immutable
checkpoint binding, all publication paths, atomic retry identity, replay
parity, malformed-snapshot rejection, and secret exclusion. Full repository
tests, Ruff, mypy, focused statement/branch coverage for changed modules, and
an independent diff review gate the feature PR.
