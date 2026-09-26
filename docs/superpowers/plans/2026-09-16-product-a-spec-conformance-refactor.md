# Product A Specification Conformance Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Incrementally bring the current `finance_research_agent` repository into behavioral and architectural conformance with the approved Product A specification and implementation plans while preserving correct deterministic market-data, regime, replay, evaluation, and security behavior.

**Architecture:** Keep the existing historical-market-data and deterministic-regime vertical slice as a characterized subsystem. Introduce the canonical Product A contracts and run/evidence boundaries beside it, bridge existing provenance into those contracts, then migrate consumers slice by slice through configuration, quality gates, Product A analysis, packet synthesis, validation, publication, replay, MCP, and skills. The generic `agent/` runtime remains isolated extra infrastructure and is not the Product A workflow authority.

**Tech Stack:** Python 3.12+, strict typed contracts, Pydantic v2 for canonical serialized Product A contracts, frozen dataclasses where they remain internal calculation values, Decimal numeric truth, YAML configuration, constrained HTTP, immutable JSON, deterministic Markdown, stdio MCP, pytest, Ruff, and mypy. Dependencies are added only in the slice that first uses them.

**Authoritative specifications and plans:**

- `docs/superpowers/specs/2026-08-19-ai-market-research-agent-premarket-design.md`
- `docs/superpowers/plans/2026-08-20-ai-market-research-agent-premarket-v0.1.md`
- `docs/superpowers/specs/2026-08-31-product-a-skill-workflow-contract-delta.md`
- `docs/superpowers/plans/2026-08-31-product-a-skill-workflow-contract-delta.md`
- `docs/superpowers/plans/2026-09-09-v04-slice-4b-replay-contracts.md`

The 2026-08-31 delta overrides only the skill/workflow portions it explicitly changes. The 2026-09-09 replay plan overrides only historical replay temporal semantics. No later roadmap document is authority for this refactor.

## Global Constraints

- Product A remains local-first, personal, research-only decision support for U.S.-listed common stocks and non-leveraged, non-inverse ETFs.
- Never introduce account, position, holdings, buying-power, order, routing, cancellation, execution, short, options, futures, crypto, streaming, or autonomous trading capability.
- Deterministic code owns values, calculations, metrics, scores, levels, sizing, gates, state transitions, and publication validation.
- Every live run freezes configuration and evidence. Evidence added after the cutoff requires a new immutable revision.
- Preserve the approved distinction among source/event time, publication time, observation time, retrieval time, evidence cutoff, decision time, and generation time.
- Provider-specific shapes terminate at adapters. Product A domain and application modules must not depend on Alpaca SDK types, Codex types, MCP schemas, or `agent/` runtime contracts.
- Do not implement Product A with `AgentRuntime`; the approved workflow is Codex plugin -> fixed skills workflow -> local stdio MCP -> deterministic application/core -> bounded `ResearchPacket` synthesis.
- Do not add MCP or Product A skills before their typed application contracts and services exist.
- Do not add all planned dependencies up front. Add and lock a dependency only in the first slice that uses it.
- Each migration slice is one focused PR, begins with failing or characterization tests, preserves public compatibility where explicitly stated, and passes the repository-wide checks before review.
- No flag-day rewrite. Existing correct behavior remains callable until its replacement path has parity tests and all internal consumers have migrated.

---

## Audit Baseline

Audit worktree baseline:

```text
HEAD: 9583698e07b27043e2a0e37279d6b1e55bf09fc5
git status --short: clean
```

The two excluded later roadmap files are absent from this worktree.

Pre-audit checks, run before any repository modification:

```text
pytest:          858 passed, 1 warning in 5.68s
                 warning: existing websockets.legacy deprecation warning
ruff check .:    All checks passed!
mypy src:        Success: no issues found in 31 source files
git diff --check: passed with no output
```

Current implementation flow:

```text
HistoricalDailyBarsRequest
        |
        v
AlpacaHistoricalBarsClient ---- request-global typed failure
        |
        v
normalize_alpaca_daily_bars
        |
        +---- HistoricalBarsFailure (per symbol)
        |
        v
HistoricalDailyBars + HistoricalBarsProvenance
        |
        v
to_market_snapshot
        |
        v
run_regime_workflow -> calculate_regime -> RegimeResult
        |
        v
regime eval / benchmark / replay / walk-forward / regression gate
```

This is a coherent specialized vertical slice. It is not yet the Product A run pipeline.

## Overall Conformance and Priorities

**Overall conformance: PARTIAL.** The repository strongly conforms to the approved deterministic numeric, market-data-only, provider-boundary, no-brokerage, immutable-value, and replay-delta principles. It does not yet implement most Product A contracts or the Product A application, publication, plugin, MCP, and fixed-skill workflow.

### P0 — Existing correctness or safety violation

None found. The full suite passes; no brokerage surface, prohibited execution state, post-cutoff live historical retrieval, or reversed domain-to-adapter dependency was found.

### P1 — Architecture blockers

1. Canonical Product A contract foundation is absent: `RunContext`, evidence contracts, instrument/price identity, gates, capabilities, packet, draft, validation, publication, and component-version contracts do not exist.
2. The current `MarketSnapshot` and `MetricResult` names represent narrower regime-specific semantics than the canonical Product A contracts. This naming/shape collision must be migrated deliberately, not ignored or overwritten.
3. No immutable run/revision identity, configuration snapshot, evidence manifest, checkpoint store, atomic publication boundary, or artifact replay service exists.
4. The current application service fetches and computes a regime directly from caller-supplied request/cutoff values; it has no authoritative Product A run state or evidence freeze owner.
5. There is no canonical provenance bridge from historical provider observations/failures into `SourceObservation` and `EvidenceItem` values.

### P2 — Required Product A capability gaps

- Configuration and watchlist services and all five policy contracts.
- Market calendar, New York run windows, revisions, leases, checkpoints, and recovery.
- Instrument identity, current price/premarket observations, official event evidence, and source authority/conflict handling.
- Data-quality and capability evaluation.
- Eligibility, event gates, setup detection, scoring, ranking, plan construction, Decimal sizing, expiry, and plan observation.
- `ResearchPacket`, bounded synthesis, `ResearchBriefDraft`, deterministic validation, rendering, publication, and immutable Product A replay.
- Product A application orchestration, exact MCP operations, plugin packaging, premarket/watchlist skills, workflow manifest, and release evaluation.

### P3 — Structural alignment

- Canonical serialized contracts must move to strict Pydantic v2 models with generated JSON Schema; internal pure calculation values may remain frozen dataclasses.
- The dependency set must grow incrementally as approved Product A capabilities are implemented.
- README, AGENTS, package version, and release documentation must be realigned with implemented Product A facts at each milestone.
- Planned responsibilities should be added to compatible layers; filenames should change only where public semantic collisions or dependency direction require it.

### P4 — Optional cleanup

- Consolidate small internal validation helpers only after a demonstrated third consumer.
- Decide at the final release gate whether legacy compatibility aliases and the unused Alpaca SDK client can be removed.
- Consider reorganizing the standalone `evals/` package only if Product A evaluation creates an actual discovery or ownership conflict. No move is currently required.

---

## Spec-to-Implementation Conformance Matrix

| Requirement | Authority document | Expected implementation | Current implementation | Evidence | Status | Required action |
|---|---|---|---|---|---|---|
| Research-only, human-review-only, no execution | Base spec sections 3, 5, 6.7, 25, 31 | No broker/account/order models or tools; only DRAFT/REVIEW_REQUIRED/BLOCKED/EXPIRED | No broker domain or endpoints; security tests scan imports and literals; generic runtime has no financial tools | `tests/security/test_alpaca_boundary.py`, `tests/security/test_model_boundary.py` | ALIGNED | Preserve as release-blocking tests in every slice |
| Deterministic numeric truth | Base spec sections 6.2, 11.3, 20, 24-26 | Core owns calculations, values, gates, scores, sizing, states | Indicators and regime are deterministic Decimal-based; remaining Product A calculations do not exist | `domain/indicators.py`, `domain/regime.py` | PARTIAL | Preserve regime core; implement missing deterministic Product A capabilities |
| Provider independence | Base spec sections 6.8, 8, 16.1 | Provider shapes end at adapters; domain depends only on normalized contracts | Alpaca SDK confined to adapter; domain and market-data contracts are provider neutral | adapter and dependency-boundary tests | ALIGNED | Keep adapter isolation; broaden normalized Product A ports without leaking SDK types |
| Run identity and revisions | Base spec section 10; master Tasks 4-5 | `premarket-YYYY-MM-DD-rN`, automatic idempotency, immutable revisions | No Product A run identity or repository | no matching source type | MISSING | Implement `RunContext`, run-window service, revision allocator, immutable repository |
| Configuration freeze | Base spec section 11.1; master Task 3 | Five validated policy snapshots and component versions frozen per revision | `RegimePolicy` only; caller supplies it directly | `domain/regime.py` | MISSING | Add strict policies, configuration snapshot/hash service, and run ownership |
| Evidence freeze | Base spec sections 6.4, 11.2; replay delta | Live cutoff bounds included evidence; historical retrieval may follow decision when supplied evidence is valid | Historical request/provenance enforce request/source/cutoff bounds; live normalizer/client reject late retrieval; no Product A evidence manifest | `market_data/historical.py`, Alpaca adapters, replay tests | PARTIAL | Keep historical semantics; add run-owned evidence freeze and canonical evidence manifest |
| Source/event/publication/retrieval semantics | Base spec sections 14-16; replay delta | Distinct typed timestamps with no look-ahead | `source_timestamp`, `retrieved_at`, `evidence_cutoff_at`, `decision_at` are distinct; event/publication/observed/generated contracts absent | historical and replay contracts | PARTIAL | Add canonical timestamp fields and explicit bridge rules; never alias publication to source time |
| Three orthogonal run statuses | Base spec section 12 | Execution, data quality, and delivery status are distinct | None | no matching enums/types | MISSING | Add closed enums and transition tests |
| Capability model | Base spec section 13; master Task 10 | Eight explicit capability states with reasons | None | no matching source type | MISSING | Add deterministic capability evaluation and expose every disabled reason |
| Canonical core contracts | Base spec section 14; master Task 2 | Strict schema-versioned Product A models | Only specialized `MarketSnapshot`, `MetricResult`, historical, regime, eval, and agent values | current source inventory | PARTIAL | Add canonical models; bridge and migrate overlapping names |
| Claims and evidence | Base spec section 15; master Tasks 2, 13 | Every fact cites evidence; calculations cite metrics; conflicts visible | No `Claim` or canonical evidence graph | no matching source type | MISSING | Implement typed evidence/claim reachability and deterministic validation |
| Market data provenance | Base spec sections 14.2, 16.1; master Task 7 | Provider/feed/coverage/session/times and quality retained | Completed history retains provider/feed/coverage/adjustment/source/retrieval/cutoff and quality; current/premarket price absent | historical contracts and tests | PARTIAL | Preserve historical records; add identity and current `PriceObservation` coverage |
| Official-source/event evidence | Base spec sections 16.2-16.4, 21; master Tasks 6, 8 | Constrained HTTP, source tiers, event verification, conflict handling | None | no HTTP or event adapters | MISSING | Implement source policy, constrained HTTP, SEC/macro/company-IR/news adapters later |
| Watchlist | Base spec section 17; master Task 3 | <=30 names, strict roles, English stored content, optimistic versions | None | no watchlist contract/service | MISSING | Add configuration and watchlist transactions before watchlist skill |
| Configuration policies | Base spec section 18 | Risk, regime, setup, source, watchlist policies | Only immutable default `RegimePolicy`; no YAML/config versioning | `domain/regime.py` | PARTIAL | Preserve regime semantics while moving values into frozen configuration snapshots |
| Report structure and language | Base spec section 19; master Tasks 13-14 | Exact English sections, conditional non-imperative language | No report contract or renderer | no matching source | MISSING | Add structured draft validation and deterministic Markdown rendering |
| Five-component regime | Base spec section 20; master Task 9 | Exact components, weights, unavailable rules, thresholds | Implemented with deterministic metrics and extensive tests | regime and indicator modules/tests | ALIGNED | Preserve behavior; extend provenance references through canonical bridge |
| Event-risk overlay | Base spec section 21; master Task 10 | Independent non-compensable event gates | None | no event module | MISSING | Add after evidence contracts and official-source normalization |
| Instrument eligibility | Base spec section 22; master Task 10 | Binary eligibility before scoring | None | no identity/eligibility modules | MISSING | Add identity contract and fail-closed gates |
| Setup detection | Base spec section 23; master Task 11 | Breakout continuation and trend pullback only | None | no setup module | MISSING | Implement only after history/identity/quality prerequisites |
| Candidate scoring/ranking | Base spec section 24; master Task 11 | Gates before score, exact weights/ties/caps | None | no scoring module | MISSING | Implement deterministic complete candidate/exclusion reporting |
| Trade plan and sizing | Base spec sections 25-27; master Task 12 | Conditional long-only plans, Decimal sizing, no approval/execution | None | no plan/sizing contracts | MISSING | Implement after gates and scoring; preserve no-execution boundary |
| Plan expiry and observation | Base spec section 28; master Task 12 | Expiry, MFE/MAE, ambiguous same-bar semantics, no P&L claim | No Product A `PlanObservation`; regime eval observation is unrelated | eval types only | MISSING | Add distinct Product A plan-observation contract and tests |
| Data quality/failure matrix | Base spec section 29; master Tasks 10, 15 | PASS/DEGRADED/FAIL and scoped capability failures | Historical layer has request-global and per-symbol typed failures; no Product A matrix | historical outcomes/tests | PARTIAL | Reuse failures as inputs to canonical quality evaluation |
| Crash recovery/atomic publication | Base spec section 30; master Tasks 5, 14-15 | Leases, checkpoints, staging, atomic rename/index | None | no filesystem/run repository | MISSING | Implement run repository before orchestration |
| Security model | Base spec section 31; master Tasks 1, 6, 16, 18 | Secret isolation, constrained URL/path surface, prompt-injection defense, no broker | Broker/model dependency boundaries exist; no constrained HTTP/MCP/prompt/publication surface yet | security tests | PARTIAL | Keep current guards; add each missing boundary with its owning feature |
| Immutable artifact layout | Base spec sections 32-33 | Canonical JSON, deterministic Markdown, no DB | None | no artifact store/renderer | MISSING | Implement frozen bundles and atomic publication |
| Testing/evaluation | Base spec sections 35-37; master Task 18 | Unit/contract/adapter/integration/replay/security/failure/25 scenarios/shadow mode | Strong regime-focused unit/adapter/eval/security suite; no Product A end-to-end scenarios or scorecard | 858 passing tests | PARTIAL | Preserve suite; add Product A gates incrementally |
| Skill contract and one manifest | 2026-08-31 delta sections 4-10 | Shared skill structure; one premarket manifest; watchlist manifest-free; bundle digest | Existing `market-regime` skill predates required structure; premarket/watchlist skills absent | `skills/` | MISSING | Implement only after typed application/MCP prerequisites |
| Replay retrieval semantics | Replay delta and base spec section 11.2.1 | Historical retrieval may follow decision; evidence cutoff remains bounded; live collection still rejects late retrieval | Implemented exactly, including failure outcomes and no clock/runner in replay case | historical and replay tests | SUPERSEDED_BY_APPROVED_DELTA | Preserve without reintroducing universal `retrieved_at <= cutoff` |
| Generic model/tool runtime | Not in Product A base plan | Product A uses fixed skills and typed MCP operations, not a model-selected generic tool loop | Bounded generic `AgentRuntime`, ports, registry, and fakes exist after original plan | `agent/`, fake adapters/tests | EXTRA_IMPLEMENTATION | Keep isolated; prohibit Product A application/domain dependencies and direct domain-tool exposure |
| Standalone regime benchmark/evals | Product A evaluation principles plus replay delta; not the full Product A scenario harness | Reuse deterministic application capabilities without owning domain truth | Implemented as isolated offline evaluation infrastructure | `evals/` | EXTRA_IMPLEMENTATION | Preserve as compatible infrastructure; do not mistake it for Product A artifact replay |

---

## Original Product A Contract Audit

The status vocabulary in this section is intentionally distinct from the conformance-matrix vocabulary.

| Planned contract | Audit status | Current semantic mapping | Required migration |
|---|---|---|---|
| `RunContext` | MISSING | No run/revision/state/configuration snapshot owner | Create canonical strict model in R1; populate through run services in R3 |
| `SourceObservation` | MISSING | `HistoricalBarsProvenance` plus `DailyBarObservation` cover part of the information | Create canonical model; bridge without discarding specialized provenance |
| `EvidenceItem` | MISSING | Historical bars and failures are evidence-bearing but not claim-addressable | Create stable evidence IDs and bounded structured fields in R1/R2 |
| `InstrumentIdentity` | MISSING | Only normalized symbol strings exist | Create strict identity before eligibility/current observations |
| `PriceObservation` | MISSING | `DailyBarObservation` is a completed-bar value, not a current/premarket price observation | Create separately; do not overload historical bars |
| `MarketSnapshot` | SPECIALIZED_IMPLEMENTATION | Current type is completed history for regime inputs only | Migrate the canonical name/shape; retain a temporary regime-input compatibility type |
| `EventRecord` | MISSING | No event model | Create after evidence/source contracts |
| `MetricResult` | SPECIALIZED_IMPLEMENTATION | Strong deterministic metric contract but references snapshot IDs, not evidence IDs | Migrate to canonical serialized shape while preserving formula/value behavior |
| `GateResult` | MISSING | Regime component reason codes are not Product A gates | Create closed gate status/reason contract |
| `CapabilityState` | MISSING | No capability model | Create and derive through quality evaluation |
| `RawSetup` | MISSING | No setup detection | Create with two setup families only |
| `SetupCandidate` | MISSING | No candidate scoring/ranking | Create after gates and setup detection |
| `TradePlanDraft` | MISSING | No plan model | Create complete long-only conditional contract |
| `Claim` | MISSING | No report claim graph | Create with fact/calculation/inference/hypothesis invariants |
| `ResearchPacket` | MISSING | No bounded synthesis input | Create after deterministic plan inputs and evidence graph exist |
| `ResearchBriefDraft` | MISSING | `FinalAnswer` is generic text and is not a Product A draft | Create strict structured Product A draft; never reuse generic final-answer text |
| `ValidationReport` | MISSING | No publication validator | Create attempt/repair-aware deterministic result |
| `PlanObservation` | MISSING | `RegimeEvalObservation` records expected/actual regimes only | Create distinct path-observation contract with no execution or P&L fields |
| `SynthesisProvenance` | MISSING | Generic model contracts deliberately omit provider metadata | Create trusted host-supplied publication provenance; never accept it from a draft/request |

## Market and Provenance Contract Decisions

| Existing contract | Decision | Reason and final relationship |
|---|---|---|
| `DailyBar` | KEEP | Correct immutable OHLCV calculation input; it remains internal normalized market data |
| `DailyBarObservation` | KEEP | Correctly binds one bar to provider `source_timestamp`; bridge it into evidence rather than renaming the timestamp |
| `HistoricalBarsProvenance` | KEEP | Correct approved replay semantics and canonical history identity; final evidence bridge references the whole provenance value |
| `HistoricalDailyBars` | KEEP | Correct available-history aggregate and deterministic identity; becomes one input to canonical market snapshots |
| `HistoricalBarsFailure` | KEEP | Correct evidence-bearing per-symbol unavailability; maps to quality/gate/evidence records, never fabricated bars |
| `HistoricalBarsRequestFailure` | KEEP | Correct request-global provider-neutral failure taxonomy; maps to run/quality failure handling |
| `HistoricalDailyBarsRequest` | EXTEND | Keep its historical request semantics; application services supply it from frozen run/configuration rather than callers owning Product A time policy |
| `MarketSnapshot` | MIGRATE | Current narrow shape conflicts with the planned canonical name. Rename the narrow implementation to an explicit regime/completed-history input, keep a temporary compatibility alias, and make canonical `MarketSnapshot` own instrument, prices, bars, observations, and quality |
| `MetricResult` | MIGRATE | Preserve formulas, values, units, periods, IDs, and flags; add canonical evidence references and strict serialization/schema behavior |
| `RegimePolicy` | EXTEND | Preserve validated semantics and defaults; make an exact frozen copy part of the versioned configuration snapshot |
| `RegimeResult` | EXTEND | Preserve classification behavior and result identity; connect component/metric outputs to canonical evidence references |
| `to_market_snapshot` | REPLACE | Split into an explicit historical-to-canonical bridge and a canonical-to-regime-input projection; remove the ambiguous single projection after all consumers migrate |

### Coherent final timestamp model

| Timestamp | Owner | Meaning and rule |
|---|---|---|
| `source_timestamp` | `DailyBarObservation` | Provider event/observation timestamp for a bar. It maps to evidence event time where applicable; it is never treated as publication availability |
| `event_time` | `EvidenceItem` / `EventRecord` | Time the represented event occurred or is scheduled to occur; future scheduled events are allowed when their supporting evidence was available by cutoff |
| `published_time` | `EvidenceItem` | Time the source publication/revision became available when known; unknown publication time remains unknown and visible |
| `observed_at` | `SourceObservation` / `PriceObservation` | Time a source or market value was observed; current market values use this rather than bar retrieval time |
| `retrieved_at` | source/provenance contracts | Time this system acquired the data. Live collection requires it no later than the run cutoff; offline historical retrieval may occur later under the replay delta |
| `evidence_cutoff_at` | `RunContext` and historical provenance | Immutable upper bound on evidence admitted to a live revision or claimed by a supplied historical dataset |
| `decision_at` | `RegimeReplayCase` | Explicit simulated historical decision time. It is not derived from retrieval or wall clock and remains eval-only |
| `generated_at` / `calculated_at` | deterministic artifact or metric owner | Time an output was generated/calculated; it cannot establish evidence availability |

The live Product A run does not add a second `decision_at` alias. Its authoritative evidence decision boundary is `RunContext.evidence_cutoff_at`. Historical replay retains explicit `decision_at` because retrieval can occur after that simulated decision.

---

## Implementation-Choice Audit

| Difference from master plan | Classification | Decision |
|---|---|---|
| Frozen dataclasses instead of Pydantic models | MUST_MIGRATE_TO_PLAN for canonical serialized Product A contracts | Introduce strict frozen Pydantic models and generated schemas. Keep internal calculation dataclasses when they are not wire/artifact contracts |
| Active package name `finance_research_agent` | COMPATIBLE_WITH_PLAN | The approved skill/workflow delta explicitly preserves the active package name; do not rename it |
| Minimal current dependency set | COMPATIBLE_WITH_PLAN | Add approved dependencies only when a migration slice uses them; do not create an unused bulk stack |
| Specialized historical-data contracts | COMPATIBLE_WITH_PLAN | Preserve and bridge them into canonical evidence and snapshot contracts |
| Standalone `evals/` package | HARMLESS_EXTRA | It is dependency-correct and reuses application/domain behavior; retain it independently of future Product A scenario evaluation |
| Generic `AgentRuntime` | HARMLESS_EXTRA while isolated | Retain it, but Product A domain/application/MCP/skills must not depend on it or treat registry membership as authorization/workflow permission |
| Alpaca SDK historical client | MUST_MIGRATE_TO_PLAN for Product A production collection | Preserve normalizer and request/failure characterization; introduce the planned constrained Product A provider boundary and retire direct production reliance only after parity |
| Existing market-regime skill shape | MUST_MIGRATE_TO_PLAN at the approved skills slice | Reformat to the 2026-08-31 contract without changing numeric behavior; do not add a manifest to this simple skill |

## Existing Public Surface Decisions

| Existing public module/type | Decision | Compatibility rule |
|---|---|---|
| package `__version__` | EXTEND | Keep aligned with `pyproject.toml`; version bumps occur only at approved release boundaries |
| `domain.market.DailyBar` | KEEP | Preserve constructor, Decimal behavior, and validation |
| `domain.market.MarketSnapshot` | MIGRATE | Introduce explicit regime-input name and temporary alias; canonical Product A snapshot becomes authoritative |
| `domain.metrics` enums | KEEP | Preserve enum meanings; extend only through additive versioned values when required |
| `domain.metrics.MetricResult` | MIGRATE | Preserve existing fields during compatibility window; add evidence references/schema contract |
| all public indicator functions | KEEP | Preserve signatures and exact formulas unless a separately approved formula version changes |
| `RegimePolicy`, component/result enums and values, `calculate_regime` | KEEP/EXTEND | Preserve behavior and IDs; add canonical evidence integration without changing classification |
| `market_data` request/feed/coverage/adjustment/provenance/outcome types | KEEP | Preserve approved temporal and failure semantics |
| `create_daily_bar_observation` | KEEP | Continue as validated normalization constructor |
| `to_market_snapshot` | REPLACE | Use two explicit bridge functions during migration; retain wrapper until callers move |
| `HistoricalBarsFetcher` | EXTEND | Retain for the regime use case; add broader Product A provider ports separately |
| `run_regime_workflow` | KEEP | Remains a deterministic inner application operation over normalized outcomes |
| `run_regime_research` and `RegimeResearchResult` | WRAP | Keep as focused use case; Product A run service calls it through frozen context and quality orchestration rather than replacing it immediately |
| `normalize_alpaca_daily_bars` and `AlpacaDailyBarRecord` | KEEP | Preserve exact normalization, per-symbol isolation, and live-cutoff behavior |
| `AlpacaHistoricalBarsClient` | MIGRATE | Keep until constrained Product A provider parity; then deprecate/remove if no approved consumer remains |
| all `evals` public types/functions | KEEP | Preserve replay delta, caller order, error propagation, and regression semantics; do not call this Product A artifact replay |
| `agent` model/tool/port/registry/runtime public surface | KEEP, isolated | No Product A domain/application import; no direct exposure of domain functions; no authorization inference |
| `FakeModelPort` and `FakeToolPort` | KEEP, isolated | Continue as offline generic-runtime test doubles; Product A protocol tests use typed application/MCP fakes |
| `market-regime` skill | EXTEND | Adopt required shared section contract later; remain manifest-free and synthetic/existing-result only |
| `skills/README.md` | EXTEND | Become link-only when the other approved skills exist; never duplicate workflow order |

No current production public type is marked `DELETE` now. Removals occur only after an explicit compatibility window and a repository-wide consumer scan proves the old surface unused.

---

## Stable Current Implementation to Preserve

1. **Daily-bar validation and deterministic history identity.** Keep exact Decimal conversion, OHLC/volume invariants, session ordering, request bounds, canonical hashing, and ambient Decimal-context independence.
2. **Historical provenance and replay delta.** Keep feed/coverage/adjustment, source timestamp, retrieval time, evidence cutoff, completed-session checks, late historical retrieval acceptance, and live normalizer/client late-retrieval rejection.
3. **Per-symbol versus request-global failure semantics.** Keep `HistoricalBarsFailure` for isolated data quality and `HistoricalBarsRequestFailure` for request-global failures; Product A quality gates consume rather than flatten them.
4. **Alpaca market-data-only boundary.** Keep the static prohibition on trading/account/position/order imports and endpoints, complete response materialization, schema/failure mapping, and absence of direct network escape hatches outside the adapter.
5. **Indicator formulas and metric identity.** Keep SMA, slope, relative return, ATR percentage, realized volatility, percentile, equal-weight basket behavior, exact Decimal semantics, and structured unavailability.
6. **Five-component regime behavior.** Keep weights, rules, thresholds, `UNKNOWN` requirements, caller-order-independent inputs, explainable components, and deterministic result IDs.
7. **Provider-neutral application delegation.** Keep `run_regime_research` fetch-once behavior and `run_regime_workflow` projection/delegation without adapter imports.
8. **Evaluation and replay semantics.** Keep single-case/aggregate reports, benchmark identity/comparison, regression tolerance, caller-order temporal validation, point-in-time replay, walk-forward binding, and unchanged error propagation.
9. **Generic runtime isolation.** Keep immutable messages/actions/observations, intent/execution separation, exact registry lookup, bounded completions, sequential execution, and fake determinism as separate infrastructure.
10. **Security architecture tests.** Keep broker isolation, adapter direction, no dynamic network/import escape hatch, and no domain/application dependency on the generic runtime.

These behaviors receive characterization coverage in R0 and must remain green through every later slice.

---

## Target Architecture

```text
Codex scheduled task / conversation
              |
              v
fixed Product A SKILL.md + one premarket workflow manifest
              |
              v
narrow typed stdio MCP operations
              |
              v
application services / composition root
  | config + run identity + checkpoint state
  | evidence collection + quality/capability evaluation
  | deterministic Product A orchestration
  | packet + validation + publication + artifact replay
              |
              v
canonical Product A domain contracts and policies
  | identity / evidence / timestamps
  | indicators + regime (preserved)
  | eligibility / events / setups / scoring / sizing
  | claims / gates / plan state / validation
              |
              v
provider and storage adapters
  | constrained HTTP and official sources
  | Alpaca market-data-only collection
  | YAML configuration
  | immutable filesystem artifacts

Isolated support surfaces:
  evals/  -> consumes application/domain contracts; never owns numeric truth
  agent/  -> generic EXTRA_IMPLEMENTATION; never owns Product A workflow
```

Target Product A data flow:

```text
resolve market date/window and immutable run revision
  -> snapshot five policies, watchlist, schemas, skills, prompt, template, code versions
  -> observe prior published plans
  -> collect market and official-source evidence under deadline
  -> freeze evidence_cutoff_at and evidence manifest
  -> normalize with provider/feed/coverage/time provenance
  -> global and per-symbol quality/capability gates
  -> deterministic market metrics and preserved regime calculation
  -> setup detection
  -> eligibility, event, timing, data, and risk gates
  -> deterministic scoring, ranking, levels, sizing, and plan inputs
  -> bounded immutable ResearchPacket
  -> bounded Codex ResearchBriefDraft synthesis from the same packet
  -> deterministic validation with at most two repairs
  -> deterministic reduced fallback when necessary
  -> atomic immutable JSON + deterministic Markdown publication
  -> zero-network artifact replay with component-version verification
```

### Planned-but-missing contracts and services

Domain/contracts: closed enums, strict base model, `RunContext`, `SourceObservation`, `EvidenceItem`, `InstrumentIdentity`, `PriceObservation`, canonical `MarketSnapshot`, canonical `MetricResult`, `EventRecord`, `GateResult`, `CapabilityState`, `RawSetup`, `SetupCandidate`, `TradePlanDraft`, `Claim`, `ResearchPacket`, `ResearchBriefDraft`, `ValidationReport`, `PlanObservation`, `SynthesisProvenance`, configuration/policy values, errors, component versions, published bundle, and supporting values.

Application: `ConfigService`, `WatchlistService`, packet service, publication service, run service, replay service, feedback service, read-only query services, trusted `ApplicationServices` composition root, clock/calendar/config/provider/repository ports, collection service, quality service, and operational-report service.

Domain behavior: market calendar/run windows, eligibility, events, quality/capabilities, setups, scoring/ranking, sizing, plan construction/expiry/observation, claim/brief validation.

Adapters and surfaces: constrained HTTP, official evidence adapters, broader Alpaca market-data provider, YAML repository, immutable filesystem repository, deterministic renderers, schema export, exact MCP server/CLI, plugin manifests, premarket/watchlist skills, one workflow manifest, Product A scenario evaluation, and shadow scorecard.

---

## Incremental Migration Plan

### R0 — Freeze Current Correct Behavior and Architecture Boundaries

**Goal:** Make the existing vertical slice an explicit, protected migration baseline before structural changes.

**Relevant spec requirements:** deterministic truth, provider independence, market-data-only security, point-in-time evidence, fail-closed behavior.

**Relevant master-plan tasks:** Tasks 1, 6-7, 9, and the replay delta.

**Files:** existing unit/adapter/application/eval/security tests; create focused architecture/characterization tests only where an invariant is currently implicit.

**Public contracts:** no production API change.

**Tests first:** add a single matrix that locks: historical available/failure/request-failure ownership; live versus historical retrieval semantics; `HistoricalDailyBars -> regime` projection; exact indicator/regime outputs; no domain/application import from `agent` or adapters; no broker surface.

**Implementation steps:**

- [ ] Inventory current exported symbols and record compatibility expectations.
- [ ] Add missing characterization assertions without changing production behavior.
- [ ] Add an import-graph test that Product A-capable layers cannot import `finance_research_agent.agent`.
- [ ] Re-run all 858+ tests and capture canonical fixture/result hashes used by later bridge tests.

**Migration compatibility:** all existing imports and behavior remain unchanged.

**Things removed:** none.

**Verification:** `pytest`, `ruff check .`, `mypy src`, `git diff --check`.

**Acceptance criteria:** current behavior is fully green; characterization fixtures cover each preserved component listed above; no source behavior changes.

**Explicit non-goals:** no Pydantic contracts, no Product A service, no rename, no MCP.

**PR boundary:** tests and architecture guards only.

### R1 — Establish Canonical Product A Foundation Contracts

**Goal:** Add the strict schema-versioned identity, run, evidence, status, and version contracts required by all later slices.

**Relevant spec requirements:** sections 10, 12-15, 31, 40.

**Relevant master-plan tasks:** Task 2 and the contract portions of Tasks 3-5.

**Files:** `domain/enums.py`, `domain/models.py`, `domain/errors.py`, `domain/types.py`, `schema_export.py`, generated schemas, and contract tests.

**Public contracts:** `RunContext`, `SourceObservation`, `EvidenceItem`, `InstrumentIdentity`, `PriceObservation`, `EventRecord`, `GateResult`, `CapabilityState`, configuration/version support values, `StrictModel`, and closed enums. Do not yet define a second competing `MarketSnapshot` or `MetricResult` name.

**Tests first:** strict extra-field rejection, UTC/timestamp role validation, immutable mappings/tuples, run ID/revision identity, closed status vocabularies, source/evidence referential shape, schema stability, prohibited plan states, no broker fields.

**Implementation steps:**

- [ ] Add Pydantic v2 and schema tooling as the first used dependencies; lock versions.
- [ ] Implement strict frozen models and canonical serialization.
- [ ] Implement closed errors/status/capability/claim/plan enums needed by later types.
- [ ] Export deterministic JSON schemas and add drift checks.
- [ ] Keep current regime and market-data modules untouched.

**Migration compatibility:** additive only; no current import moves.

**Things removed:** none.

**Verification:** contract tests, schema check, current full suite, Ruff, mypy, diff check.

**Acceptance criteria:** canonical foundational values serialize deterministically, reject unknown fields, and contain no provider/runtime-specific types.

**Explicit non-goals:** canonical snapshot/metric migration, configuration I/O, run storage, business logic.

**PR boundary:** foundation contracts and schemas only.

### R2 — Bridge Historical Provenance into Canonical Evidence and Market Contracts

**Goal:** Connect the correct existing historical slice to canonical Product A evidence without weakening either contract family.

**Relevant spec requirements:** sections 11.2, 14-16, 20; replay delta.

**Relevant master-plan tasks:** Tasks 2, 7, 9, 13.

**Files:** `domain/market.py`, `domain/metrics.py`, `domain/models.py`, `market_data/historical.py`, a focused application bridge module, and compatibility tests.

**Public contracts:** canonical `MarketSnapshot` and `MetricResult`; explicit regime/completed-history input type; bridge functions from historical outcomes to source/evidence/snapshot values and from canonical snapshots to regime inputs.

**Tests first:** one-to-one provenance preservation, stable evidence IDs, failure-as-evidence mapping, feed/coverage/session disclosure, no publication-time invention, canonical metric evidence reachability, old-versus-new regime result parity.

**Implementation steps:**

- [ ] Rename the narrow current snapshot implementation to an explicit regime/completed-history name while retaining a documented compatibility alias.
- [ ] Define canonical Product A `MarketSnapshot` with instrument, latest price, completed/current bars, source observations, and quality flags.
- [ ] Migrate `MetricResult` to strict serialized form while temporarily retaining `input_snapshot_ids`; add required `input_evidence_ids`.
- [ ] Split `to_market_snapshot` into explicit historical-to-canonical and canonical-to-regime projections; keep a compatibility wrapper.
- [ ] Update regime internals through the bridge and prove byte/value/classification parity.

**Migration compatibility:** current public imports continue through aliases/wrappers for at least one subsequent slice; deprecation is documented but not warned at runtime.

**Things removed:** no public symbol; only ambiguous private helpers may be replaced.

**Verification:** historical, indicator, regime, application, replay, and schema suites plus full checks.

**Acceptance criteria:** every regime metric can reach canonical evidence; replay semantics are unchanged; no provider field is lost; canonical and legacy paths produce identical regime behavior.

**Explicit non-goals:** current quotes, events, config, setups, publication.

**PR boundary:** provenance and canonical market/metric bridge only.

### R3 — Add Configuration, Market Time, Run Identity, and Immutable Storage

**Goal:** Establish authoritative Product A run/configuration/state ownership before broader collection or business logic.

**Relevant spec requirements:** sections 10-12, 17-18, 30, 32.

**Relevant master-plan tasks:** Tasks 3-5.

**Files:** policies/config services, market calendar, application ports, YAML and filesystem adapters, run-state tests.

**Public contracts:** all five policies, `ConfigurationSnapshot`, `RunWindowDecision`, clock/calendar/config/run repository ports, lease/checkpoint/stored-run/publication metadata values.

**Tests first:** 30-name limit; strict policy examples; canonical hashes; optimistic watchlist versioning; DST/holiday/late-window boundaries; automatic r1 idempotency/manual revision increment; lease expiry; immutable cutoff; staging/path safety.

**Implementation steps:**

- [ ] Add PyYAML and exchange-calendar dependencies when first used.
- [ ] Implement strict policy models and safe, non-operational examples.
- [ ] Implement parse-validate-normalize-hash-snapshot configuration flow and atomic watchlist transactions.
- [ ] Implement New York run-window decisions and legal execution transitions.
- [ ] Implement allowlisted filesystem repository, leases, checkpoints, revisions, and staging; publication remains unavailable.

**Migration compatibility:** `RegimePolicy` values remain semantically identical and can be projected from the frozen configuration model.

**Things removed:** none.

**Verification:** focused config/time/storage tests, all preserved behavior, full checks.

**Acceptance criteria:** one immutable run revision owns its configuration copy, cutoff state, and checkpoint history; no caller path or secret enters public contracts.

**Explicit non-goals:** provider HTTP, Product A analysis, synthesis, final publication.

**PR boundary:** trusted local state and configuration foundation only.

### R4 — Build Product A Evidence Collection and Constrained Provider Boundaries

**Goal:** Collect identity, history, current observations, and official/discovery evidence through approved typed ports and one security boundary.

**Relevant spec requirements:** sections 16, 21, 29, 31.

**Relevant master-plan tasks:** Tasks 6-8.

**Files:** constrained HTTP/settings, broader Alpaca provider, official-source adapters, application collection ports/services, adapter/security tests.

**Public contracts:** `MarketDataProvider`, `EventProvider`, `EventCollection`, `SourceHealth`, `ProviderReadiness`, `ProviderFailure`, constrained request/response values.

**Tests first:** SSRF/redirect/content/size/timeout/retry/redaction; identity and IEX provenance; current price freshness; schema drift; per-symbol isolation; official-source authority; discovery-only news; cutoff exclusion; source conflict retention.

**Implementation steps:**

- [ ] Add HTTPX and settings dependencies when used; implement secret-free settings and constrained HTTP.
- [ ] Build `AlpacaMarketDataProvider` behind Product A ports, reusing the existing historical normalizer.
- [ ] Add current `PriceObservation` and `InstrumentIdentity` normalization.
- [ ] Add SEC, macro, company-IR, and bounded discovery adapters with typed health/failure output.
- [ ] Retain the SDK historical client during parity; mark it non-authoritative for Product A collection.

**Migration compatibility:** existing historical client/tests remain green; new provider is additive until parity and composition-root adoption.

**Things removed:** none in this PR; direct production reliance on the SDK client is removed only after the new provider becomes the sole composition-root path.

**Verification:** adapter/security tests with network denied, full suite, Ruff, mypy, diff check.

**Acceptance criteria:** all Product A evidence is typed, bounded, provenance-rich, cutoff-checked, and provider-independent outside adapters.

**Explicit non-goals:** quality decisions, setups, plans, MCP, live smoke in CI.

**PR boundary:** collection/security adapters and typed collection service only.

### R5 — Implement Quality, Capability, Eligibility, and Event Gates

**Goal:** Convert source health and evidence into deterministic scoped availability and non-overridable Product A gates.

**Relevant spec requirements:** sections 13, 21-22, 29.

**Relevant master-plan tasks:** Task 10 and failure-matrix inputs from Task 15.

**Files:** `domain/quality.py`, `domain/eligibility.py`, `domain/events.py`, focused unit tests.

**Public contracts:** `DataQualityResult`, `EventAssessment`, gate evaluators, capability evaluator.

**Tests first:** each failure-matrix dependency, global/capability/symbol scope, required macro failure, stale current data, source conflict, earnings window, halt/corporate action/identity, role/price/liquidity/history eligibility.

**Implementation steps:**

- [ ] Implement exact eight capability states and PASS/DEGRADED/FAIL aggregation.
- [ ] Map existing historical failures into scoped quality and gate outcomes without flattening.
- [ ] Implement binary instrument/watchlist eligibility before scoring.
- [ ] Implement independent event overlay with authority/conflict/cutoff rules.

**Migration compatibility:** historical failure types remain unchanged inputs; no current regime behavior changes.

**Things removed:** none.

**Verification:** quality/eligibility/event tests plus preserved data/regime suites and full checks.

**Acceptance criteria:** every disabled capability has a stable reason; no score or narrative can override a blocking gate.

**Explicit non-goals:** setup formulas, ranking, sizing, report prose.

**PR boundary:** deterministic gates only.

### R6 — Implement Setup Detection, Scoring, and Ranking

**Goal:** Add the two approved setup families and complete, deterministic candidate ordering.

**Relevant spec requirements:** sections 23-24.

**Relevant master-plan tasks:** Task 11.

**Files:** `domain/setups.py`, `domain/scoring.py`, setup/scoring fixtures and tests, methodology updates.

**Public contracts:** `RawSetup`, plan-level support values, `SetupCandidate`, `CandidateExclusion`, detection/level/scoring/ranking functions.

**Tests first:** valid/invalid breakout and pullback; gates-before-score; exact weights and penalties; threshold boundaries; tie order; five-plan/three-highlight caps; duplicate exposure; zero-candidate success.

**Implementation steps:**

- [ ] Implement deterministic plan-level calculations over canonical snapshots/metrics.
- [ ] Implement only breakout continuation and trend pullback.
- [ ] Reject blocked/missing-required-data candidates before component evaluation.
- [ ] Rank every scored candidate deterministically and retain exclusions/near misses.

**Migration compatibility:** reuses preserved indicators/regime and canonical gates; does not alter them.

**Things removed:** none.

**Verification:** setup/scoring tests, domain suites, full checks.

**Acceptance criteria:** all candidates and exclusions remain explainable; no data gap is compensated by a score.

**Explicit non-goals:** trade-plan prose, sizing, synthesis, publication.

**PR boundary:** candidate generation and ordering only.

### R7 — Implement Trade Plans, Decimal Sizing, Expiry, and Observation

**Goal:** Produce complete conditional research plan inputs and post-publication path observations without execution semantics.

**Relevant spec requirements:** sections 25-28.

**Relevant master-plan tasks:** Task 12.

**Files:** canonical plan support models, `domain/sizing.py`, `domain/observations.py`, plan/sizing/observation tests.

**Public contracts:** `TradePlanDraft`, `PositionSizing`, plan builder, sizing calculator, expiry function, `PlanObservation`, observation function.

**Tests first:** every required plan field; long-only and allowed statuses; stop/entry/target relationships; exact Decimal intermediates and rounding; unavailable reasons; portfolio heat limitation; expiry reasons; ambiguous same-bar sequence; MFE/MAE after entry only; absence of execution/P&L fields.

**Implementation steps:**

- [ ] Build plans only from selected candidates and canonical gates/evidence.
- [ ] Implement exact sizing equations and persist every intermediate.
- [ ] Cap unknown heat/manual conditions at `REVIEW_REQUIRED`.
- [ ] Implement expiry and conservative completed-bar observation semantics.

**Migration compatibility:** no existing public behavior changes.

**Things removed:** none.

**Verification:** plan/sizing/observation tests, all deterministic domain tests, full checks.

**Acceptance criteria:** zero-to-five conditional plans are complete, expiring, evidence-linked, and never imply approval, execution, fills, or realized returns.

**Explicit non-goals:** packet synthesis, Markdown, artifact publication.

**PR boundary:** deterministic plan domain only.

### R8 — Build the Bounded Research Packet and Deterministic Draft Validator

**Goal:** Establish the only allowed Product A synthesis boundary.

**Relevant spec requirements:** sections 6.2-6.4, 11.4, 14-15, 19, 38.

**Relevant master-plan tasks:** Task 13.

**Files:** `application/packet_service.py`, `domain/validation.py`, canonical prompt, packet/validation/security tests.

**Public contracts:** `ResearchPacket`, `ResearchBriefDraft`, `Claim`, `ValidationIssue`, `ValidationReport`, packet builder, brief validator.

**R8 interface correction (approved 2026-09-26):** The packet builder accepts the frozen R6 candidate exclusions as `exclusions: Sequence[CandidateExclusion]`; `ResearchPacket` stores them as immutable `candidate_exclusions: tuple[CandidateExclusion, ...]`. Copy every supplied exclusion with its symbol, optional setup type, reason codes, and gates intact, preserving the deterministic R6 input order. Candidate exclusions are protected packet content and are never trimmed. If protected content alone exceeds the packet budget, fail with the packet budget error. Do not infer, rank, or rewrite exclusions in R8. This closes the Task 13 interface gap and supports the approved Product A requirement that all watchlist names remain visible with a concise state or exclusion reason (spec section 19.2).

**Tests first:** deep immutability/stable hash; post-cutoff rejection; protected trimming order; packet budget failure; exact sections; numeric/unit equality; evidence relevance/reachability; counter-evidence; IEX language; disabled-capability disclosure; imperative/prohibited states; repair attempts 1-3; inert prompt injection.

**Implementation steps:**

- [ ] Build one canonical packet from frozen run/evidence/deterministic outputs.
- [ ] Implement deterministic size budgeting that never removes risks/provenance/gates.
- [ ] Add one canonical prompt source and independent digest.
- [ ] Implement schema-first structured validation with no fuzzy numeric truth.

**Migration compatibility:** generic `FinalAnswer`/`ModelResponse` remain unchanged but are not accepted as Product A drafts.

**Things removed:** none.

**Verification:** packet/validation/security tests, schema drift, full checks.

**Acceptance criteria:** synthesis can narrate only packet truth; invalid drafts cannot become publishable; repairs use the same packet hash.

**Explicit non-goals:** provider model integration, generic AgentRuntime integration, publication, MCP.

**PR boundary:** packet/prompt/validator only.

### R9 — Implement Checkpointed Product A Orchestration, Publication, and Artifact Replay

**Goal:** Complete the deterministic application pipeline from frozen run preparation through atomic publication and zero-network replay.

**Relevant spec requirements:** sections 9-12, 29-30, 32, 38.

**Relevant master-plan tasks:** Tasks 14-15.

**Files:** run/collection/quality/operational-report services, publication/replay services, deterministic renderers/templates, integration/replay/failure tests.

**Public contracts:** prepare request/result, publication/replay results, published bundle/artifact, report render context, performance telemetry, trusted composition dependencies.

**Tests first:** ordered checkpoints; zero candidates; symbol isolation; global hard fail; no refresh after cutoff; crash resume at every checkpoint; 14-row failure matrix; late-window boundaries; exact report sections; atomic failure; reduced report; idempotent submission; byte-identical replay; component mismatch.

**Implementation steps:**

- [ ] Implement prepare pipeline through one frozen packet and staged reduced report.
- [ ] Implement state-authorized validation/publication and at most two repairs.
- [ ] Render deterministic Markdown and atomically publish immutable JSON/Markdown.
- [ ] Implement frozen-artifact replay with no provider/config-current-state/synthesis call.
- [ ] Record telemetry, versions, failures, and operational events without secrets.

**Migration compatibility:** `run_regime_research` remains an inner use case invoked through frozen dependencies; standalone eval replay remains separate.

**Things removed:** the temporary direct Product A invocation path, if one was introduced for integration scaffolding; no current v0.5 public API is removed.

**Verification:** application/integration/replay/security suites, full checks, complete diff review.

**Acceptance criteria:** Product A can prepare, degrade/fail safely, resume, publish atomically, and replay without Codex/MCP/provider dependence.

**Explicit non-goals:** MCP transport, plugin installation, recurring schedule activation, production model provider.

**PR boundary:** complete deterministic Product A application boundary only.

### R10 — Expose the Approved MCP, CLI, Plugin, and Skill/Workflow Contracts

**Goal:** Add the approved user/orchestration surface only after deterministic application prerequisites are stable.

**Relevant spec requirements:** sections 7-9, 31.3-31.4, 39.

**Relevant master-plan tasks:** Tasks 16-17 as overridden by the 2026-08-31 delta Tasks 1-6.

**Files:** `mcp_server/`, application services/composition root, CLI, plugin manifests, three skills, one premarket manifest, component-version digest, contract/protocol tests.

**Public contracts:** exact eleven Product A MCP operations and strict request/result/error schemas; `ApplicationServices`; skill bundle digest; premarket/watchlist skill contracts.

**Tests first:** exact tool list/schemas; no caller path/URL/deadline/provider/risk/provenance; run-state authorization; stdio/no socket; redacted errors; CLI diagnostic-only; shared skill structure; one manifest only; operation allowlist; frozen packet repairs; digest/replay mismatch; link-only index.

**Implementation steps:**

- [ ] Build one trusted environment composition root over R9 services.
- [ ] Register exact typed MCP handlers explicitly and expose stdio only.
- [ ] Add diagnostic/replay-only CLI.
- [ ] Reformat `market-regime` without behavior change; add premarket/watchlist skills and one adjacent manifest.
- [ ] Package exact skill bytes, freeze bundle digest into run provenance, and add documentation drift checks.
- [ ] Keep the schedule definition paused; do not activate it in this slice.

**Migration compatibility:** `agent/` remains unused by Product A; registry membership is neither MCP authorization nor workflow permission.

**Things removed:** obsolete duplicated skill workflow prose; no generic runtime code.

**Verification:** MCP contract/process, CLI, skill/protocol/replay, packaging, security, docs, and full repository checks.

**Acceptance criteria:** Codex follows the fixed manifest/skill protocol over exact typed operations; deterministic application remains authoritative; no unlisted tool or execution capability is reachable.

**Explicit non-goals:** production model adapter, generalized workflow engine, skill registry, active recurring schedule.

**PR boundary:** transport/plugin/skills surface only.

### R11 — Complete Product A Evaluation, Release Gates, and Compatibility Cleanup

**Goal:** Prove end-to-end conformance and remove only migration scaffolding that has no remaining consumer.

**Relevant spec requirements:** sections 35-41 and acceptance criteria.

**Relevant master-plan tasks:** Task 18 plus the 2026-08-31 verification/documentation delta.

**Files:** Product A evaluation/scenario/scorecard modules, manifests/rubrics, CI and checking scripts, release/security/operations documentation, compatibility consumer tests.

**Public contracts:** evaluation scenario/outcome, recorded feedback, shadow scorecard, citation-entailment sample, release-gate outputs.

**Tests first:** exact 25 scenarios; failure injection; append-only feedback; 20-day gates; no P&L/execution fields; tracked-data/secret/plugin boundaries; schemas/docs/skills drift; fresh-environment rehearsal.

**Implementation steps:**

- [ ] Add the 25 Product A scenarios and shadow scorecard over production services.
- [ ] Add offline CI, schema/docs/secret/security/replay gates and local doctor.
- [ ] Scan all callers of compatibility aliases and the legacy SDK client.
- [ ] Remove a compatibility alias/client only when no supported consumer remains and replacement parity is proven; otherwise retain and document it.
- [ ] Align README, AGENTS, package version, changelog, and operator docs with implemented facts.
- [ ] Run the opt-in market-data-only live smoke separately; keep CI offline.

**Migration compatibility:** published artifacts remain immutable; schema migrations derive new views and never rewrite history.

**Things removed:** only unused compatibility wrappers/aliases and superseded duplicated documentation proven safe by consumer scans. `AgentRuntime` and standalone regime evals remain unless a separate approved cleanup decision says otherwise.

**Verification:** fresh environment, full offline suite, exact scenarios, security/replay suites, schema/docs/secret checks, Ruff, mypy, diff check, optional live market-data-only smoke.

**Acceptance criteria:** all Product A acceptance criteria and release gates pass; shadow mode can begin; no-execution boundary is unchanged.

**Explicit non-goals:** claims of alpha/profitability, shadow-mode graduation, execution, Product B/C/D, second provider, marketplace publication.

**PR boundary:** end-to-end evaluation and release readiness; schedule activation remains a separately reviewed operational action.

---

## Characterization Test Map

| Behavior to protect | Existing evidence | Additional pre-migration characterization |
|---|---|---|
| Daily-bar and snapshot validation | `tests/unit/test_market.py` | Golden canonical serialization for bridge inputs |
| Historical identity/provenance | `tests/unit/test_historical_market_data.py` | Evidence bridge preserves every timestamp/feed/coverage/adjustment field |
| Alpaca normalization and failure isolation | `tests/adapters/test_alpaca_daily_bars.py` | Canonical `EvidenceItem` and quality projection parity |
| Alpaca SDK request/error boundary | `tests/adapters/test_alpaca_historical_client.py` | Parity fixture for the constrained Product A provider before deprecation |
| Indicator formulas and IDs | `tests/unit/test_indicators.py` | Old/new metric serialization and value parity |
| Regime policy/results | `tests/unit/test_regime.py` | Canonical snapshot bridge produces identical `RegimeResult` |
| Application delegation/error propagation | `tests/application/` | Frozen `RunContext` wrapper does not change fetch count or exceptions |
| Historical replay delta | `tests/evals/test_regime_replay.py` | Product A artifact replay test explicitly distinguishes itself from eval replay |
| Walk-forward semantics | `tests/evals/test_temporal.py`, `test_walk_forward_replay.py` | None unless a Product A consumer is added |
| Benchmark/regression behavior | benchmark/report/regression tests | None; keep isolated |
| Broker and dependency boundaries | `tests/security/test_alpaca_boundary.py` | Add Product A domain/application -> `agent/` import prohibition |
| Generic runtime semantics | `tests/agent/`, fake adapter tests | Add isolation test only; do not adapt it into Product A |

## Existing Public APIs Affected

- `domain.market.MarketSnapshot`: compatibility alias during R2, then canonical Product A snapshot becomes the documented authority.
- `domain.metrics.MetricResult`: gains canonical evidence references and schema behavior while preserving values/formula IDs during migration.
- `market_data.to_market_snapshot`: retained as a wrapper during R2, then replaced by explicit bridge functions after consumer migration.
- `HistoricalBarsFetcher` and regime application functions: retained; Product A ports/services are additive wrappers.
- `RegimePolicy`/`RegimeResult`/indicators/evals: behavior preserved; evidence/configuration integration is additive.
- `AlpacaHistoricalBarsClient`: retained until constrained provider parity; possible final removal is conditional on a no-consumer scan.
- `agent/*`: no API change planned; explicitly excluded from Product A workflow authority.
- New Product A contracts, services, schemas, MCP operations, skills, and artifacts are additive until the compatibility cleanup in R11.

## Recommended First Implementation PR

**R0 — characterization tests and architecture-boundary guards.** It has the lowest semantic risk and creates the evidence needed to migrate `MarketSnapshot`, `MetricResult`, historical provenance, and application layering safely. It must not add a Product A contract, dependency, adapter, or source behavior.

Do not begin R0 until the human reviews and approves this migration plan. Approval of this plan does not authorize later slices, commits, pushes, MCP, plugin installation, or schedule activation; each slice keeps its own review gate.
