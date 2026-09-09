# v0.4 Slice 4B Replay Contracts Implementation Plan

The provenance adjustment and replay design were approved on 2026-09-09.
This focused plan follows the repository's tool-agnostic Change Process.

Base: `7d0483b793a99e8b08463431c89367b52d664880`.
Specification: [Section 11.2.1](../specs/2026-08-19-ai-market-research-agent-premarket-design.md#1121-historical-regime-replay-contracts-v04-slice-4b).

## Scope and boundaries

- Python 3.12 or newer; all source, tests, and documentation in English.
- Use existing provenance fields, error types, evaluator, and numeric core.
- Add no runtime dependencies, I/O, clocks, adapters, or process/Git dependencies
  to the eval layer.
- No replay runner, walk-forward binding, downloader, trading, backtesting,
  persistence, external services, or CI gates.
- Create one focused PR and leave it unmerged.

## 1. Separate historical retrieval from live evidence freeze

Files:

- `src/finance_research_agent/market_data/historical.py`
- `src/finance_research_agent/adapters/alpaca.py`
- `tests/unit/test_historical_market_data.py`
- `tests/adapters/test_alpaca_daily_bars.py`

Add offline regressions showing that neutral provenance accepts retrieval
after its evidence cutoff, while request/source bounds, completed sessions,
UTC requirements, and provenance-sensitive history identity remain enforced.
Add standalone normalizer coverage for late retrieval on both available bars
and missing-data responses, plus equality at the cutoff.

Run the focused tests before changing implementation; the neutral late-retrieval
case must fail under the existing contract. Remove only the provenance upper
bound on retrieval. In `_provenance`, construct validated provenance, explicitly
reject `provenance.retrieved_at > request.evidence_cutoff_at`, and return it.
The SDK client's existing independent retrieval guard stays unchanged.

Verify with the historical-data unit tests and both Alpaca adapter test modules.

## 2. Add the immutable replay boundary

Files:

- Create `src/finance_research_agent/evals/regime_replay.py`
- Modify `src/finance_research_agent/evals/__init__.py`
- Create `tests/evals/test_regime_replay.py`

Public interface: `RegimeReplayCase(case: RegimeEvalCase, decision_at: datetime)`.
Use `@dataclass(frozen=True, slots=True)` and construction-time validation.
Require an actual `RegimeEvalCase`, explicit UTC-aware zero-offset decision and
case cutoff values, and `case.cutoff_at == decision_at`. Check every outcome is
an existing available-history or failure value with validated provenance, then
require its evidence cutoff no later than decision time. Preserve the original
case and outcomes; do not scan bars or recompute regime results.

Add offline tests before implementation for earlier/equal/future evidence,
future evidence in every outcome position and in failures, invalid and missing
decision times, timezone behavior, mismatched evaluator cutoffs, late retrieval,
immutability, and invalid runtime types. Exercise the unchanged evaluator via
`evaluate_regime_case(replay.case)`, verifying existing programmer/domain errors
propagate. Enforce absence of clock calls in the new module and run the existing
eval import boundary guard. Source/session regressions stay in market-data tests.

## 3. Document, verify, and publish for review

Files:

- `README.md`
- `docs/superpowers/specs/2026-08-19-ai-market-research-agent-premarket-design.md`
- This implementation plan

Document source/event time, evidence cutoff, retrieval time, decision time,
the live adapter restriction, and the historical publication/revision limitation.
Correct the previous README statement that all provenance constrains retrieval
to the evidence cutoff. State that optional runner and walk-forward integration
are deferred.

Run `.venv/bin/pytest`, `.venv/bin/ruff check .`, `.venv/bin/mypy src`, and
`git diff --check`. Inspect `git diff --stat` and the complete diff, including new
files. Confirm dependency declarations and numeric algorithms are unchanged.
Commit and push the focused branch, create one PR targeting `main`, and verify
its base, head, file list, and open/unmerged state.
