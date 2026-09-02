import ast
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import finance_research_agent.application.regime_workflow as workflow_module
from finance_research_agent.application.regime_workflow import run_regime_workflow
from finance_research_agent.domain.market import DailyBar, MarketDataSource, MarketSnapshot
from finance_research_agent.domain.regime import (
    Regime,
    RegimePolicy,
    RegimeResult,
    calculate_regime,
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

CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 25, 12, 40, tzinfo=UTC)


def _provenance() -> HistoricalBarsProvenance:
    return HistoricalBarsProvenance(
        provider="test-provider",
        feed=MarketDataFeed.IEX,
        coverage=MarketDataCoverage.SINGLE_EXCHANGE,
        adjustment=BarAdjustment.SPLIT,
        requested_start_at=datetime(2026, 8, 24, 4, tzinfo=UTC),
        requested_end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        retrieved_at=RETRIEVED_AT,
        evidence_cutoff_at=CUTOFF,
        completed_through_session=date(2026, 8, 24),
        adapter_version="test-adapter-v1",
    )


def _history(symbol: str) -> HistoricalDailyBars:
    observation = DailyBarObservation(
        source_timestamp=datetime(2026, 8, 24, 4, tzinfo=UTC),
        bar=DailyBar(
            session_date=date(2026, 8, 24),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=1_000_000,
        ),
    )
    return HistoricalDailyBars.create(
        symbol=symbol,
        observations=(observation,),
        provenance=_provenance(),
        quality_flags=("TEST_DATA",),
    )


def _failure(symbol: str) -> HistoricalBarsFailure:
    return HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol=symbol,
        reason=HistoricalBarsUnavailableReason.NO_DATA,
        provenance=_provenance(),
        missing_sessions=(date(2026, 8, 24),),
        quality_flags=("NO_DATA",),
    )


def test_projects_only_available_histories_into_the_numeric_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available = _history("SPY")
    unavailable = _failure("QQQ")
    policy = RegimePolicy()
    expected = calculate_regime({}, policy, CUTOFF)
    projected: list[HistoricalDailyBars] = []
    received_snapshots: Mapping[str, MarketSnapshot] | None = None
    real_projection = workflow_module.to_market_snapshot

    def tracking_projection(history: HistoricalDailyBars) -> MarketSnapshot:
        projected.append(history)
        return real_projection(history)

    def capture_calculation(
        snapshots: Mapping[str, MarketSnapshot],
        received_policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        nonlocal received_snapshots
        received_snapshots = snapshots
        return expected

    monkeypatch.setattr(workflow_module, "to_market_snapshot", tracking_projection)
    monkeypatch.setattr(workflow_module, "calculate_regime", capture_calculation)

    result = run_regime_workflow((available, unavailable), policy, CUTOFF)

    assert result is expected
    assert projected == [available]
    assert received_snapshots is not None
    assert tuple(received_snapshots) == ("SPY",)
    snapshot = received_snapshots["SPY"]
    assert isinstance(snapshot, MarketSnapshot)
    assert snapshot.symbol == "SPY"
    assert snapshot.source is MarketDataSource.NORMALIZED_PROVIDER
    assert snapshot.completed_daily_bars == (available.observations[0].bar,)


def test_calls_calculate_regime_once_with_unchanged_policy_and_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = RegimePolicy()
    expected = calculate_regime({}, policy, CUTOFF)
    calls: list[tuple[Mapping[str, MarketSnapshot], RegimePolicy, datetime]] = []

    def capture_calculation(
        snapshots: Mapping[str, MarketSnapshot],
        received_policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        calls.append((snapshots, received_policy, cutoff_at))
        return expected

    monkeypatch.setattr(workflow_module, "calculate_regime", capture_calculation)

    result = run_regime_workflow((), policy, CUTOFF)

    assert result is expected
    assert len(calls) == 1
    snapshots, received_policy, received_cutoff = calls[0]
    assert snapshots == {}
    assert received_policy is policy
    assert received_cutoff is CUTOFF


def _assert_duplicate_symbol_rejected_before_downstream_calls(
    outcomes: tuple[HistoricalBarsOutcome, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projected: list[HistoricalDailyBars] = []
    calculations: list[Mapping[str, MarketSnapshot]] = []
    expected = calculate_regime({}, RegimePolicy(), CUTOFF)
    real_projection = workflow_module.to_market_snapshot

    def tracking_projection(history: HistoricalDailyBars) -> MarketSnapshot:
        projected.append(history)
        return real_projection(history)

    def tracking_calculation(
        snapshots: Mapping[str, MarketSnapshot],
        policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        calculations.append(snapshots)
        return expected

    monkeypatch.setattr(workflow_module, "to_market_snapshot", tracking_projection)
    monkeypatch.setattr(workflow_module, "calculate_regime", tracking_calculation)

    with pytest.raises(ValueError, match="SPY"):
        run_regime_workflow(outcomes, RegimePolicy(), CUTOFF)

    assert projected == []
    assert calculations == []


def test_rejects_duplicate_available_histories_before_downstream_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_duplicate_symbol_rejected_before_downstream_calls(
        (_history("SPY"), _history("SPY")), monkeypatch
    )


def test_rejects_available_and_failure_for_the_same_symbol_before_downstream_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_duplicate_symbol_rejected_before_downstream_calls(
        (_history("SPY"), _failure("SPY")), monkeypatch
    )


def test_rejects_duplicate_failures_before_downstream_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_duplicate_symbol_rejected_before_downstream_calls(
        (_failure("SPY"), _failure("SPY")), monkeypatch
    )


def test_failure_remains_missing_and_domain_owns_unknown_semantics() -> None:
    result = run_regime_workflow((_failure("SPY"),), RegimePolicy(), CUTOFF)

    assert isinstance(result, RegimeResult)
    assert result.regime is Regime.UNKNOWN
    assert "broad_trend:missing_required_snapshot" in result.unavailable_reasons


def test_application_workflow_has_no_alpaca_or_adapter_dependency() -> None:
    application_root = Path(workflow_module.__file__).resolve().parent

    for path in application_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            module == "alpaca"
            or module.startswith("alpaca.")
            or module == "finance_research_agent.adapters"
            or module.startswith("finance_research_agent.adapters.")
            for module in imported_modules
        ), (path, imported_modules)


def test_projection_programmer_errors_are_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    programmer_error = RuntimeError("projection bug")

    def fail_projection(history: HistoricalDailyBars) -> MarketSnapshot:
        raise programmer_error

    monkeypatch.setattr(workflow_module, "to_market_snapshot", fail_projection)

    with pytest.raises(RuntimeError, match="projection bug") as raised:
        run_regime_workflow((_history("SPY"),), RegimePolicy(), CUTOFF)

    assert raised.value is programmer_error


def test_regime_programmer_errors_are_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    programmer_error = RuntimeError("regime bug")

    def fail_calculation(
        snapshots: Mapping[str, MarketSnapshot],
        policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        raise programmer_error

    monkeypatch.setattr(workflow_module, "calculate_regime", fail_calculation)

    with pytest.raises(RuntimeError, match="regime bug") as raised:
        run_regime_workflow((), RegimePolicy(), CUTOFF)

    assert raised.value is programmer_error
