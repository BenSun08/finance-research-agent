# Product A Run Initialization Design

Date: 2026-09-28
Status: Draft for review

## Purpose

Define the atomic handoff between Product A run preparation and immutable run
storage. The preparation service must freeze the configuration it actually
loaded into a run revision, while the repository must continue assigning
revision identities safely under concurrent calls.

This design closes a gap between R3's acceptance criterion (each immutable run
owns its configuration snapshot) and R9's `RunDependencies` (the preparation
service loads that configuration). The current `RunRepository.allocate_revision`
creates a context containing placeholder configuration and component versions,
so the preparation service cannot satisfy both requirements without changing
the boundary.

## Goals and Constraints

- Persist the actual validated `ConfigurationSnapshot` and component versions
  in the first immutable `RunContext` for a revision.
- Preserve repository-owned, atomic revision assignment and the current run ID
  format `premarket-YYYY-MM-DD-rN`.
- Reuse a scheduled run's frozen r1 and resume an exact requested unpublished
  manual revision with its original frozen inputs.
- Never mutate a persisted `RunContext` to apply newly loaded configuration.
- Keep calendar, invocation-window, configuration, and status decisions in the
  application/domain layers; the repository only assigns identity and stores
  values.
- Keep caller paths, credentials, provider payloads, callbacks, and execution
  policy out of the public seed and repository contract.

## Proposed Contract

Add a strict, frozen `RunContextSeed` domain value with:

- `market_date: date`
- `invoked_at: UtcDatetime`
- `delivery_status: DeliveryStatus`
- `configuration_snapshot: ConfigurationSnapshot`
- `component_versions: ComponentVersions`

The seed contains all non-identity values needed to construct a new initial
context. `ExecutionStatus.CREATED` and `DataQualityStatus.PASS` remain the
initial values. The repository derives `run_id` and `revision`; callers cannot
select either through the seed. The repository verifies that the policy
versions in `component_versions` match the versions in the configuration
snapshot before persisting.

For R9, the preparation service constructs `component_versions` from the
repository's declared version values: the package version, the existing 0.1
MCP/plugin/skill/prompt/report-template versions, the `run-context` schema
version, and policy versions copied from `configuration_snapshot`. R10 may
replace those declared surface versions with generated skill/protocol digests;
that future version-source change does not alter this seed or allocation
contract.

Change the repository contract to:

```python
def allocate_revision(
    self,
    seed: RunContextSeed,
    invocation: InvocationType,
    requested_revision: int | None = None,
) -> RunContext: ...
```

Allocation and initial persistence remain one operation under the existing
per-market-date repository lock. A returned existing context always retains
its already-frozen configuration, even if the caller's current seed differs.
Before loading current configuration, the preparation service checks for an
existing scheduled r1 or explicitly requested manual revision with
`RunRepository.load`. It resumes an eligible staged context directly and
rejects a published manual revision. A published scheduled r1 remains an
idempotent identity result; this seed contract returns its `RunContext` and
does not load publication artifacts. If no run exists, the service loads
configuration and invokes allocation. The allocator repeats identity checks
under its lock, so concurrent callers cannot overwrite or duplicate the first
context.

## Allocation Semantics

- **Scheduled:** allocate r1 when absent; otherwise return the existing r1
  context, whether staged or published. A duplicate scheduled call never
  creates another revision or replaces frozen inputs.
- **Manual without a requested revision:** allocate one greater than the
  highest existing staged or published revision.
- **Manual with a requested revision:** resume that exact staged, unpublished
  revision when it exists; otherwise create only the next unambiguous revision
  (`max_existing + 1`). Reject a revision gap, an existing published
  revision, or any conflicting identity. A published revision remains
  immutable.
- **New revision:** construct and persist its `RunContext` from the seed with
  the allocated identity and initial statuses. No placeholder context is
  written first.

An explicit requested revision is valid only for a manual invocation. Invalid
invocation/revision combinations fail before storage mutation. Existing run
directory/path confinement, locking, and immutable staging rules continue to
apply.

## Alternatives Considered

1. **Replace an initial placeholder context.** Rejected because it mutates a
   supposedly immutable run and can leave revision identity without its
   configuration snapshot after a crash.
2. **Have application code choose a revision and call `create`.** Rejected
   because `get_latest` only exposes published revisions and a read-then-create
   sequence cannot safely coordinate concurrent manual invocations.
3. **Pass a callback to the repository to construct the context.** Rejected
   because storage would execute application code while holding its lock and
   the repository port would gain an executable dependency.
4. **Pass an immutable seed to the atomic allocator.** Recommended: storage
   continues to own revision uniqueness while application code owns the
   validated run inputs and business decisions.

## Responsibilities and Data Flow

1. The preparation service reads one UTC instant from `Clock` and resolves the
   requested market date and `RunWindowDecision` with the injected calendar.
2. For an eligible scheduled r1 or exact manual revision, it checks the
   deterministic run ID with `RunRepository.load`. An existing eligible
   unpublished revision resumes from its stored context without reading current
   configuration; an explicitly requested published manual revision is
   rejected. A published scheduled r1 is returned as the existing immutable
   context; mapping that state to the full preparation result remains the
   responsibility of the R9 run service.
3. If no eligible existing revision is found, the service loads and validates
   current configuration once, builds its immutable `ConfigurationSnapshot`,
   obtains the applicable component-version value, and creates
   `RunContextSeed` using those values and the resolved delivery status.
4. It calls `RunRepository.allocate_revision(seed, invocation,
   requested_revision)`. Under its lock, the repository repeats the identity
   check to handle concurrent allocation, then either returns the existing
   frozen context or atomically creates a new one from the seed.
5. Subsequent checkpoints and frozen artifacts refer to that returned context;
   configuration reloads cannot change it.

Non-trading-day, too-early scheduled, missed-window, and calendar-error result
handling remain governed by the existing run-window and operational-report
contracts. This design does not add a new report policy for those outcomes.

## Failure and Compatibility

- Malformed seeds, inconsistent policy-version metadata, scheduled explicit
  revisions, revision gaps, and attempts to resume published manual revisions
  fail before allocating or changing run state.
- Existing `allocate_revision(market_date, invocation, now)` callers must move
  to the seed-based signature in the same migration slice; repository identity
  and path safety behavior remain unchanged.
- No production model, provider, scheduling, MCP, or brokerage capability is
  introduced.

## Verification Requirements

Focused tests must prove:

- a new context contains the exact supplied configuration snapshot, component
  versions, invocation time, delivery status, and allocated identity;
- duplicate scheduled allocation returns r1 with its original frozen snapshot;
- manual allocation increments monotonically and remains unique under
  concurrent repository instances;
- an exact staged unpublished manual revision resumes with the stored snapshot;
- only the next unambiguous explicit revision can be created;
- revision gaps, published explicit revisions, scheduled explicit revisions,
  and policy-version mismatches fail without creating new staging state; and
- persisted contexts are never updated in place.

The implementation remains subject to the approved R9 TDD, independent review,
coverage analysis, full repository checks, commit, PR, CI, and merge process.
