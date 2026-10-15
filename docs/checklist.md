# Requirements and Readiness Checklist

This checklist assesses clarity, consistency and readiness. It is not an
implementation task list or proof that software works; those records belong in
[tasks](tasks.md). Reviewed against the confirmed design on 2026-10-14.

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
