# Requirements and Readiness Checklist

This checklist assesses clarity, consistency and readiness. It is not an
implementation task list or proof that software works; those records belong in
[tasks](tasks.md). Product design reviewed on 2026-10-14; technical planning
evidence and remaining readiness obligations updated on 2026-10-23.

## Design quality

- [x] Product outcome includes decisions, simulated execution, holdings and evaluation.
- [x] Real-money execution is excluded and simulation is explicitly labeled.
- [x] First instruments, direction, quantity convention, account/currency and cadence are stated.
- [x] Known candidates precede full-market discovery.
- [x] Six role responsibilities, challenge and supplementary-round bound are explicit.
- [x] Subjective judgment and deterministic financial/state authority are distinct.
- [x] Required evidence and scoped failure behavior are specified without assuming sources exist.
- [x] Human cycle/policy control and per-order simulated authorization are consistent.
- [x] Local durability, recovery and historical artifact preservation are required.
- [x] Dashboard scope does not depend on unproven direct Work launching.
- [x] Exact replay, new model experiments and forward results have distinct meanings.
- [x] Memory has as-of visibility and cannot autonomously change policy.
- [x] Incremental cost ceiling and unobservable shared usage are explicit.
- [x] Requirements have acceptance statements and task mappings.
- [x] Existing behavior and approved future capabilities are labeled separately.
- [x] Each document category has one owner; executable artifacts retain their roles.

## Implementation readiness

- [ ] RQ-01: actual Work/local transport, role delegation, structured returns and resume demonstrated.
- [ ] RQ-02: required-source matrix, availability, licenses and instrument/action-specific gates approved.
- [ ] RQ-03: settlement, gaps, cash, stops, ambiguity, corporate actions, order lifecycle and pause rules resolved.
- [ ] RQ-04: evaluation formulas, matched baselines, missing-day handling and release claims approved.
- [ ] RQ-05: concrete incremental-cost inventory and practical workload/stop limits verified.
- [ ] Versioned persisted/interface contracts and compatibility strategy reviewed.
- [ ] First vertical-slice file scope, tests and failure cases approved before implementation.

An unchecked implementation item blocks its dependent work, not completion of
the documentation pass. Product approval is complete; technical readiness remains
conditional. Do not mark these items complete from code presence or a plausible
design alone.

The planning pass supplies concrete answers for review without changing the
16/23 checked count. The seven remaining items have the following evidence gap:

| Readiness item | Design now available | Still required |
| --- | --- | --- |
| RQ-01 | Typed host authority, role and resume/receipt contracts | Actual Work invocation and permission/delegation/recovery trace |
| RQ-02 | Action-specific source/capability inventory, raw-price and temporal rules | Approved source/license/coverage/cost rows and normalized samples |
| RQ-03 | Event schedule, state tables, numeric/accounting profile and S fixtures | Review of proposed choices, calendar/event coverage, approved risk inputs and executable fixtures |
| RQ-04 | Separate evaluation contract, benchmark selector/allocation, metric availability and E fixtures | Reviewed versions and executable replay/recovery fixtures |
| RQ-05 | Incremental inventory and bounded synthetic workload design | Complete quotes, account visibility, measured workload and stop controls |
| Versioned contracts | `trading.v1` record/operation design, atomic persistence and compatibility strategy | Strict schema/consumer fixtures and contract review before activation |
| First slice | Proposed module/file scope and required failure cases | Feasibility exits and explicit bounded implementation approval |

See the [remaining gate evidence](research.md#remaining-gate-evidence) for closure
criteria and [bounded delivery](plan.md#project-structure-and-bounded-delivery)
for dependencies. No new material product clarification is required by this
planning revision. Required policy values and observed external capabilities
remain explicit inputs/gates; they are not guessed to make the checklist pass.
