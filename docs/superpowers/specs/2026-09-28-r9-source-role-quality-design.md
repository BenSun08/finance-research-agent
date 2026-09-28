# R9 Source-Role Quality Contract Design

Date: 2026-09-28
Status: Approved direction; written-spec review pending

## Purpose

Align Product A data-quality evaluation with the currently approved source
scope. Runs must distinguish a required configured source that is unavailable
from a source capability that is outside the approved run scope. Neither case
may be represented by invented successful source health.

## Scope and constraints

- Product A remains research-only and uses only currently approved source
  capabilities.
- The initial R9 run scope requires Alpaca market data and the trading calendar.
- SEC and macro source roles are not configured or fetched by this design.
- Domain quality evaluation remains deterministic and derives its source-role
  requirements from the immutable source policy captured in the run.
- Missing health for a configured role remains a configuration failure.
- A configured required market-data or market-calendar source failure remains
  a global hard failure.
- Out-of-scope roles must have explicit capability-unavailable reasons so
  downstream packets and reports cannot imply that their checks ran.

## Design

### Frozen source-role declaration

Add an explicit immutable `quality_source_roles: tuple[SourceRole, ...]`
declaration to `SourcePolicy`. `SourceRole` is a closed domain enum with
`MARKET_DATA = "market-data"`, `MARKET_CALENDAR = "market-calendar"`,
`MACRO_CALENDAR = "macro-calendar"`, and
`OFFICIAL_VERIFICATION = "official-verification"` values. The declaration is
not inferred from health records supplied by callers. The initial example
configuration declares only the currently approved roles: `MARKET_DATA` and
`MARKET_CALENDAR`. The declaration is serialized into the existing
`ConfigurationSnapshot`, so resumed runs use the same quality scope even when
current configuration changes.

The source-policy validator requires a tuple with unique role values and
requires both `MARKET_DATA` and `MARKET_CALENDAR` in every Product A policy.
Macro and official-verification roles may be added only in a separately
approved source-scope change.

The role set is separate from `allowed_adapters`: adapter allowlists constrain
network-capable adapters, while source roles declare which quality inputs a
run is expected to evaluate. In particular, the market calendar is an injected
domain dependency and is not represented as an HTTP adapter.

### Quality evaluation

`evaluate_data_quality` and `evaluate_capabilities` receive the declared role
set from the frozen source policy. For each configured role, evaluation
requires exactly one matching required `SourceHealth` record using an approved
provider identity for that role. Duplicate, missing, or mismatched health
records remain configuration errors. Health is never synthesized from a role
declaration alone.

For roles omitted from the policy, quality evaluation adds a stable
`SOURCE_NOT_CONFIGURED` reason to the capabilities that depend on that role.
Those states are unavailable and the aggregate result is `DEGRADED` when the
remaining required inputs are healthy. The initial mapping preserves current
domain dependencies:

- `market-data` and `market-calendar` are global prerequisites; an unavailable
  configured prerequisite yields `FAIL`.
- `macro-calendar` restricts event-risk, setup detection, plan drafting,
  position sizing, and portfolio-heat capabilities, matching the current
  `_dependencies` mapping.
- `official-verification` restricts plan-draft availability.

The initial frozen policy omits the latter two roles. No provider adapters,
network reads, or new event/filing data are added. Existing role-specific
provider failures continue to map through the same deterministic capability
dependencies.

`evaluate_collected_market_data_quality` accepts the frozen `SourcePolicy` (or
its closed role set) and forwards it to domain evaluation. The run integration
must obtain it only from `RunContext.configuration_snapshot`, never from
invocation arguments or current configuration.

### Compatibility

All callers of quality evaluation declare a role set explicitly; there is no
implicit compatibility default. The R9 preparation path always passes the
frozen role set. Tests and direct domain callers are updated to use explicit
roles so accidental fallback to a broader implicit source scope is not
possible. `SourcePolicy` examples and construction sites are updated together;
existing snapshot hashing continues to cover the new field.

## Failure behavior

- Missing health for a configured role: `FAIL` with `CONFIGURATION_INVALID`;
  evaluation does not assume availability.
- Unavailable required market-data or market-calendar role: `FAIL` with its
  reported closed error code.
- Health or failure records for a provider belonging to an unconfigured role
  are rejected; a caller cannot smuggle an out-of-scope source result into the
  run.
- Role excluded by frozen policy: dependent capabilities are unavailable with
  `SOURCE_NOT_CONFIGURED`; no health record is fabricated.
- Symbol-scoped collection failure: remains scoped to that symbol and does not
  become a global failure.
- Malformed provider identity or contradictory role/health inputs: reject as
  invalid input before packet or publication staging.

## Tests and acceptance criteria

Tests must prove:

- the role declaration is immutable, closed, validated, and included in the
  canonical configuration snapshot hash;
- the R9 example policy contains only the currently approved roles;
- healthy Alpaca and market-calendar health permits quality evaluation without
  requiring SEC/macro records;
- missing or unavailable configured health remains a hard failure;
- omitted macro/official-verification roles disable only their declared
  dependent capabilities and produce `DEGRADED`, never `PASS`;
- provider failures preserve current global versus symbol scope;
- collection quality evaluation derives role scope from the frozen policy; and
- packet construction and reduced-report rendering disclose unavailable
  capabilities without adding market claims.

No implementation beyond the source-role quality reconciliation is included in
this design. Implementing the R9 run orchestration remains a subsequent slice.

## Alternatives considered

1. **Infer roles from supplied health records.** Rejected because omission
   would silently change quality semantics and could disguise an unobserved
   required source as out of scope.
2. **Pass role requirements at each call site.** Rejected because callers could
   choose different semantics for the same immutable run.
3. **Keep all four roles mandatory.** Rejected because the pipeline could not
   complete within the approved source scope without adding prohibited
   providers or fabricating health.
