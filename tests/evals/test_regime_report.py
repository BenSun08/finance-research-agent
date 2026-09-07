from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

import finance_research_agent.evals.regime as eval_module
from finance_research_agent.domain.market import InvalidMarketDataError
from finance_research_agent.domain.regime import Regime, RegimePolicy
from finance_research_agent.evals import (
    RegimeEvalCase,
    RegimeEvalObservation,
    RegimeEvalReport,
    TagEvalSummary,
    evaluate_regime_cases,
)

CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)


def _case(
    case_id: str,
    expected_regime: Regime = Regime.UNKNOWN,
    tags: tuple[str, ...] = (),
) -> RegimeEvalCase:
    return RegimeEvalCase(
        case_id=case_id,
        outcomes=(),
        policy=RegimePolicy(),
        cutoff_at=CUTOFF,
        expected_regime=expected_regime,
        tags=tags,
    )


def test_all_pass_report() -> None:
    report = evaluate_regime_cases((_case("first"), _case("second")))

    assert report.total == report.passed == 2
    assert report.failed == 0
    assert report.accuracy == 1.0
    assert all(observation.passed for observation in report.observations)
    assert report.tag_summaries == ()


def test_mixed_report_metrics() -> None:
    report = evaluate_regime_cases(
        (_case("first"), _case("wrong", Regime.PERMISSIVE), _case("last"))
    )

    assert report.total == 3
    assert report.passed == 2
    assert report.failed == 1
    assert report.accuracy == pytest.approx(2 / 3)
    assert report.passed + report.failed == report.total


def test_evaluates_each_exact_case_once_in_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = (_case("z-last-alphabetically"), _case("a-first-alphabetically"))
    calls: list[RegimeEvalCase] = []
    observations: list[RegimeEvalObservation] = []
    real_evaluate = eval_module.evaluate_regime_case

    def capture_case(case: RegimeEvalCase) -> RegimeEvalObservation:
        calls.append(case)
        observation = real_evaluate(case)
        observations.append(observation)
        return observation

    monkeypatch.setattr(eval_module, "evaluate_regime_case", capture_case)

    report = evaluate_regime_cases(cases)

    assert len(calls) == len(cases)
    assert all(received is original for received, original in zip(calls, cases, strict=True))
    assert tuple(observation.case_id for observation in report.observations) == tuple(
        case.case_id for case in cases
    )
    assert all(
        recorded is returned
        for recorded, returned in zip(report.observations, observations, strict=True)
    )


def test_confusion_counts_use_expected_then_actual_with_stable_zero_cells() -> None:
    report = RegimeEvalReport(
        observations=(
            RegimeEvalObservation("correct", Regime.NEUTRAL, Regime.NEUTRAL),
            RegimeEvalObservation("wrong-1", Regime.DEFENSIVE, Regime.PERMISSIVE),
            RegimeEvalObservation("wrong-2", Regime.DEFENSIVE, Regime.PERMISSIVE),
        ),
        tag_summaries=(),
    )

    counts = report.confusion_counts

    assert counts[(Regime.DEFENSIVE, Regime.PERMISSIVE)] == 2
    assert counts[(Regime.PERMISSIVE, Regime.DEFENSIVE)] == 0
    assert counts[(Regime.NEUTRAL, Regime.NEUTRAL)] == 1
    assert counts[(Regime.UNKNOWN, Regime.UNKNOWN)] == 0
    assert tuple(counts) == tuple((expected, actual) for expected in Regime for actual in Regime)
    assert len(counts) == 16
    assert sum(counts.values()) == report.total
    assert sum(counts[(regime, regime)] for regime in Regime) == report.passed
    with pytest.raises(TypeError):
        counts[(Regime.UNKNOWN, Regime.UNKNOWN)] = 99  # type: ignore[index]


@pytest.mark.parametrize("duplicate", [False, True], ids=["empty", "duplicate-ids"])
def test_rejects_invalid_collections_before_evaluating(
    duplicate: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (_case("same"), _case("same", Regime.DEFENSIVE)) if duplicate else ()
    calls: list[RegimeEvalCase] = []

    def unexpected_evaluation(case: RegimeEvalCase) -> RegimeEvalObservation:
        calls.append(case)
        raise AssertionError("invalid collection must be rejected before evaluation")

    monkeypatch.setattr(eval_module, "evaluate_regime_case", unexpected_evaluation)

    with pytest.raises(ValueError, match="same" if duplicate else "empty"):
        evaluate_regime_cases(cases)

    assert calls == []


def test_rejects_mutable_case_collection() -> None:
    with pytest.raises(ValueError, match="immutable tuple"):
        evaluate_regime_cases([_case("one")])  # type: ignore[arg-type]


def test_overlapping_tags_count_each_case_once_per_tag() -> None:
    cases = (
        _case("overlap", tags=("stress", "normal")),
        _case("wrong", Regime.DEFENSIVE, tags=("stress", "missing_data")),
        _case("normal", tags=("normal",)),
        _case("untagged"),
    )

    report = evaluate_regime_cases(cases)

    assert report.total == 4
    assert report.passed == 3
    assert report.failed == 1
    assert report.accuracy == 0.75
    assert report.tag_summaries == (
        TagEvalSummary(tag="missing_data", total=1, passed=0),
        TagEvalSummary(tag="normal", total=2, passed=2),
        TagEvalSummary(tag="stress", total=2, passed=1),
    )
    assert tuple(summary.failed for summary in report.tag_summaries) == (1, 0, 1)
    assert tuple(summary.accuracy for summary in report.tag_summaries) == (0.0, 1.0, 0.5)
    assert sum(summary.total for summary in report.tag_summaries) == 5


def test_reports_replay_deterministically_independent_of_tag_order() -> None:
    case = _case("one", tags=("stress", "missing_data"))
    first = evaluate_regime_cases((case,))
    repeated = evaluate_regime_cases((case,))
    reordered = evaluate_regime_cases((replace(case, tags=tuple(reversed(case.tags))),))

    assert first == repeated == reordered
    assert first.confusion_counts == repeated.confusion_counts == reordered.confusion_counts


@pytest.mark.parametrize("domain_error", [False, True], ids=["programmer", "domain"])
def test_evaluation_error_propagates_and_stops_remaining_cases(
    domain_error: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (_case("first"), _case("broken"), _case("not-reached"))
    if domain_error:
        cases = (cases[0], replace(cases[1], cutoff_at=datetime(2026, 8, 25)), cases[2])
    programmer_error = RuntimeError("evaluation bug")
    calls: list[str] = []
    real_evaluate = eval_module.evaluate_regime_case

    def evaluate_with_error(case: RegimeEvalCase) -> RegimeEvalObservation:
        calls.append(case.case_id)
        if case.case_id == "broken" and not domain_error:
            raise programmer_error
        return real_evaluate(case)

    monkeypatch.setattr(eval_module, "evaluate_regime_case", evaluate_with_error)

    with pytest.raises(InvalidMarketDataError if domain_error else RuntimeError) as raised:
        evaluate_regime_cases(cases)

    assert calls == ["first", "broken"]
    if domain_error:
        assert "timezone-aware UTC" in str(raised.value)
    else:
        assert raised.value is programmer_error


def test_report_and_tag_summary_are_frozen() -> None:
    report = evaluate_regime_cases((_case("one", tags=("normal",)),))

    with pytest.raises(FrozenInstanceError):
        report.observations = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.tag_summaries[0].passed = 0  # type: ignore[misc]
    with pytest.raises(ValueError, match="immutable tuple"):
        replace(report, observations=list(report.observations))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="immutable tuple"):
        replace(report, tag_summaries=list(report.tag_summaries))  # type: ignore[arg-type]


def test_report_rejects_empty_observations() -> None:
    with pytest.raises(ValueError, match="empty"):
        RegimeEvalReport(observations=(), tag_summaries=())


def test_report_rejects_duplicate_observation_ids() -> None:
    report = evaluate_regime_cases((_case("one"),))

    with pytest.raises(ValueError, match="one"):
        replace(report, observations=report.observations * 2)


@pytest.mark.parametrize(("total", "passed"), [(0, 0), (-1, 0), (1, -1), (1, 2)])
def test_tag_summary_rejects_invalid_counts(total: int, passed: int) -> None:
    with pytest.raises(ValueError):
        TagEvalSummary(tag="normal", total=total, passed=passed)


@pytest.mark.parametrize("tag", [" ", " Stress ", "stress ", "Stress", "stréss", "stress__case"])
def test_tag_summary_rejects_noncanonical_tag(tag: str) -> None:
    with pytest.raises(ValueError, match="tag"):
        TagEvalSummary(tag=tag, total=1, passed=1)
