"""Frozen synthetic, capability-oriented regime benchmark v1.

This corpus is not representative of real market frequencies and does not
measure future predictive alpha, trading performance, LLM research quality,
or live provider reliability. Its weekday-only schedule is synthetic, not an
exchange calendar: holidays are deliberately not modeled. IEX/coverage values
fill the existing historical contract; they do not represent observed IEX data.
Gold labels and the full existing policy configuration are authored here, not
derived from evaluator outputs. Changes to this corpus require a new version.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from finance_research_agent.domain.market import DailyBar
from finance_research_agent.domain.regime import Regime, RegimeComponent, RegimePolicy
from finance_research_agent.evals._metadata import require_canonical_token
from finance_research_agent.evals.regime import (
    RegimeEvalCase,
    RegimeEvalReport,
    _require_unique_case_ids,
    evaluate_regime_cases,
)
from finance_research_agent.market_data.historical import (
    FAILURE_SCHEMA_VERSION,
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsOutcome,
    HistoricalBarsProvenance,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    MarketDataCoverage,
    MarketDataFeed,
)

__all__ = [
    "RegimeBenchmark",
    "RegimeBenchmarkRun",
    "build_regime_benchmark_v1",
    "run_regime_benchmark",
]


@dataclass(frozen=True, slots=True)
class RegimeBenchmark:
    """Named, versioned frozen cases sharing one complete policy value."""

    name: str
    version: str
    cases: tuple[RegimeEvalCase, ...]

    def __post_init__(self) -> None:
        require_canonical_token(self.name, "benchmark name")
        require_canonical_token(self.version, "benchmark version")
        if not isinstance(self.cases, tuple):
            raise ValueError("benchmark cases must be an immutable tuple")
        if not self.cases:
            raise ValueError("benchmark cases must not be empty")
        _require_unique_case_ids(tuple(case.case_id for case in self.cases))
        if any(case.policy != self.cases[0].policy for case in self.cases):
            raise ValueError("benchmark cases must use the same complete policy")


@dataclass(frozen=True, slots=True)
class RegimeBenchmarkRun:
    """Benchmark and existing policy identity attached to the original eval report."""

    benchmark_name: str
    benchmark_version: str
    policy_version: str
    report: RegimeEvalReport

    def __post_init__(self) -> None:
        require_canonical_token(self.benchmark_name, "benchmark name")
        require_canonical_token(self.benchmark_version, "benchmark version")


def run_regime_benchmark(benchmark: RegimeBenchmark) -> RegimeBenchmarkRun:
    """Evaluate the frozen cases once and attach identity without duplicating metrics."""

    report = evaluate_regime_cases(benchmark.cases)
    return RegimeBenchmarkRun(
        benchmark_name=benchmark.name,
        benchmark_version=benchmark.version,
        policy_version=benchmark.cases[0].policy.version,
        report=report,
    )


def _benchmark_v1_policy() -> RegimePolicy:
    """Pin existing regime-policy-v1 inputs independently of future default changes."""

    return RegimePolicy(
        version="regime-policy-v1",
        broad_symbols=("SPY", "QQQ"),
        small_cap_symbol="IWM",
        credit_asset_symbol="HYG",
        credit_benchmark_symbol="LQD",
        sector_symbols=(
            "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLK", "XLU",
        ),
        cyclical_symbols=("XLY", "XLE", "XLF", "XLI", "XLB", "XLK"),
        defensive_symbols=("XLP", "XLV", "XLU"),
        short_sma_window=50,
        long_sma_window=200,
        slope_lookback=20,
        relative_return_window=20,
        atr_window=14,
        realized_volatility_window=20,
        percentile_history=252,
        participation_positive_minimum=7,
        participation_negative_maximum=4,
        participation_minimum_valid=9,
        leadership_vote_threshold=2,
        leadership_minimum_valid=2,
        volatility_positive_maximum=Decimal("60"),
        volatility_negative_minimum=Decimal("80"),
        permissive_threshold=Decimal("35"),
        defensive_threshold=Decimal("-35"),
        component_weights=(
            (RegimeComponent.BROAD_TREND, Decimal("30")),
            (RegimeComponent.PARTICIPATION, Decimal("25")),
            (RegimeComponent.LEADERSHIP, Decimal("20")),
            (RegimeComponent.VOLATILITY_STRESS, Decimal("15")),
            (RegimeComponent.CREDIT_CROSS_ASSET, Decimal("10")),
        ),
    )


def _synthetic_sessions() -> tuple[date, ...]:
    """273 fixed weekdays provide the existing windows plus percentile history."""

    sessions: list[date] = []
    session = date(2026, 8, 24)
    while len(sessions) < 273:
        if session.weekday() < 5:
            sessions.append(session)
        session -= timedelta(days=1)
    return tuple(reversed(sessions))


def _make_compound_history(
    symbol: str,
    daily_return: Decimal,
    sessions: tuple[date, ...],
    provenance: HistoricalBarsProvenance,
) -> HistoricalDailyBars:
    """Generate only input bars; never call indicators, classification, or evaluation."""

    observations: list[DailyBarObservation] = []
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        close = Decimal("100")
        for index, session in enumerate(sessions):
            open_price = close
            if index:
                close *= Decimal(1) + daily_return
            observations.append(
                DailyBarObservation(
                    source_timestamp=datetime.combine(session, time(21), tzinfo=UTC),
                    bar=DailyBar(
                        session_date=session,
                        open=open_price,
                        high=max(open_price, close) * Decimal("1.005"),
                        low=min(open_price, close) * Decimal("0.995"),
                        close=close,
                        volume=1_000_000,
                    ),
                )
            )
    return HistoricalDailyBars.create(
        symbol=symbol,
        observations=tuple(observations),
        provenance=provenance,
        quality_flags=("SYNTHETIC",),
    )


def build_regime_benchmark_v1() -> RegimeBenchmark:
    """Build four explicitly labeled scenarios without executing the research workflow."""

    policy = _benchmark_v1_policy()
    sessions = _synthetic_sessions()
    cutoff_at = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
    provenance = HistoricalBarsProvenance(
        provider="synthetic",
        feed=MarketDataFeed.IEX,
        coverage=MarketDataCoverage.SINGLE_EXCHANGE,
        adjustment=BarAdjustment.RAW,
        requested_start_at=datetime.combine(sessions[0], time(0), tzinfo=UTC),
        requested_end_at=datetime(2026, 8, 24, 23, 59, 59, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 25, 12, 40, tzinfo=UTC),
        evidence_cutoff_at=cutoff_at,
        completed_through_session=sessions[-1],
        adapter_version="synthetic-benchmark-v1",
    )

    # Uniform relative returns keep leadership/credit mixed. Rising/falling
    # broad trend and participation alone outweigh any volatility component.
    # Flat prices imply mixed broad trend, weak participation, and low stress:
    # the authored policy's neutral interval contains that combination.
    scenarios = (
        ("risk_on", Decimal("0.001"), Regime.PERMISSIVE, ("normal",)),
        ("neutral", Decimal("0"), Regime.NEUTRAL, ("normal",)),
        ("risk_off", Decimal("-0.001"), Regime.DEFENSIVE, ("stress",)),
    )
    cases = tuple(
        RegimeEvalCase(
            case_id=case_id,
            outcomes=tuple(
                _make_compound_history(symbol, daily_return, sessions, provenance)
                for symbol in policy.required_symbols
            ),
            policy=policy,
            cutoff_at=cutoff_at,
            expected_regime=expected_regime,
            tags=tags,
        )
        for case_id, daily_return, expected_regime, tags in scenarios
    )
    # Missing SPY is not projected; the domain owns the resulting UNKNOWN.
    missing_spy = HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol="SPY",
        reason=HistoricalBarsUnavailableReason.NO_DATA,
        provenance=provenance,
        missing_sessions=sessions,
        quality_flags=("NO_DATA", "SYNTHETIC"),
    )
    missing_outcomes: tuple[HistoricalBarsOutcome, ...] = tuple(
        missing_spy if outcome.symbol == "SPY" else outcome for outcome in cases[0].outcomes
    )
    missing_case = RegimeEvalCase(
        case_id="missing_broad_data",
        outcomes=missing_outcomes,
        policy=policy,
        cutoff_at=cutoff_at,
        expected_regime=Regime.UNKNOWN,
        tags=("missing_data",),
    )
    return RegimeBenchmark(
        name="deterministic-regime",
        version="v1",
        cases=(*cases, missing_case),
    )
