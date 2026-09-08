# Finance Research Agent

An open-source AI-assisted market research and portfolio decision-support system.

## Goals

This project is designed to explore practical AI Agent Engineering concepts including:

- Agent Skills
- deterministic financial tools
- structured market data
- declarative workflows
- evaluations
- human-in-the-loop decision making
- agent runtimes
- MCP
- production automation

## Safety Boundary

This project is designed for research and decision support.

It does not automatically execute trades.

Human approval remains the final decision gate.

## Development Roadmap

- v0.1 Deterministic Research Core + First Market Regime Skill
- v0.2 Data Layer
- v0.3 Workflow
- v0.4 Evals
- v0.5 Agent Runtime
- v0.6 MCP
- v0.7 Automation / Production

## Status

v0.1.0 is released with a deterministic, synthetic-data-only market-regime core.
v0.2 includes an offline-tested, market-data-only Alpaca historical daily-bars
client that maps fully materialized SDK responses into the provider-independent
normalization boundary. Default tests require no credentials or network access;
trading remains unavailable.

v0.3 adds provider-independent application orchestration around historical-data
and deterministic market-regime capabilities. The application depends on the
provider-neutral `HistoricalBarsFetcher` port, supplied by the Alpaca adapter;
request-global failures remain provider-neutral. The workflow remains
offline-testable, and trading remains unavailable.

## Deterministic regime benchmark comparison

v0.4 includes a frozen synthetic benchmark with four explicitly labeled cases.
Each `RegimeBenchmarkRun` keeps separate identities for the benchmark definition
(`benchmark_name`, `benchmark_version`), evaluation configuration
(`policy_version`), and system under test (`system_revision`). Its original
`RegimeEvalReport` owns all metrics: total, passed, failed, accuracy, confusion
counts, and tag summaries.

Supply the revision explicitly for every run:

```python
from finance_research_agent.evals import (
    build_regime_benchmark_v1,
    run_regime_benchmark,
)

run = run_regime_benchmark(
    build_regime_benchmark_v1(),
    system_revision="build_2026_09_08",
)
```

Revisions are preserved exactly and must match
`[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*`: ASCII letters or digits with optional single
dot, underscore, or hyphen separators. Git SHAs, `v0.5.0`, and
`build_2026_09_08` are valid. Blank values, whitespace, non-ASCII characters,
paths, and repeated or leading/trailing separators are rejected before evaluation.
The caller is responsible for identifying the code actually evaluated; assigning
a revision does not switch code. Eval code does not discover revisions from Git,
shell commands, environment variables, or network access.

Use `compare_regime_benchmark_runs(baseline, candidate)` on runs produced by the
respective systems. It requires equal benchmark names, benchmark versions, and
policy versions, raising `ValueError` for a mismatch before reading accuracy.
System revisions may differ; the same revision is also valid for replay checks.
The immutable `RegimeBenchmarkComparison` contains `baseline_revision`,
`candidate_revision`, and `accuracy_delta`. The delta is always
`candidate.report.accuracy - baseline.report.accuracy`: 0.75 to 1.00 gives +0.25
(25 percentage points), and the reverse gives -0.25. Comparison reads report
accuracy directly and does not evaluate cases again.

`formula_version` is deferred in this slice. The public domain `RegimeResult`
contains it, but the existing evaluation observation/report contract does not
retain it, and the domain exposes no public formula-version constant. This slice
preserves that boundary without extra evaluation or copying a version literal.
Comparability therefore does not currently check formula versions. Benchmark
version remains the explicit corpus contract; callers must update the benchmark
version for corpus changes and the policy version for configuration changes.
No benchmark fingerprint or execution timestamp is added. These synthetic results
measure deterministic scenario behavior, not predictive alpha or trading performance.
