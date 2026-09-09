import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import finance_research_agent.evals.regime as eval_module
import finance_research_agent.evals.walk_forward_replay as walk_forward_module
from finance_research_agent.domain.market import InvalidMarketDataError
from finance_research_agent.domain.regime import Regime, RegimePolicy
from finance_research_agent.evals import (
    RegimeEvalCase,
    RegimeEvalObservation,
    RegimeEvalReport,
    RegimeReplayCase,
    TagEvalSummary,
    WalkForwardReplay,
    WalkForwardReplayPlan,
    WalkForwardWindow,
    build_regime_benchmark_v1,
    evaluate_regime_cases,
    evaluate_walk_forward_replay,
    validate_walk_forward_plan,
)


def _window(eval_year: int = 2021, train_start_year: int = 2018) -> WalkForwardWindow:
    return WalkForwardWindow(
        train_start=datetime(train_start_year, 1, 1, tzinfo=UTC),
        train_end=datetime(eval_year - 1, 12, 31, 23, 59, 59, tzinfo=UTC),
        eval_start=datetime(eval_year, 1, 1, tzinfo=UTC),
        eval_end=datetime(eval_year, 12, 31, 23, 59, 59, tzinfo=UTC),
    )


def _replay(case_id: str, decision_at: datetime) -> RegimeReplayCase:
    return RegimeReplayCase(
        case=RegimeEvalCase(
            case_id=case_id,
            outcomes=(),
            policy=RegimePolicy(),
            cutoff_at=decision_at,
            expected_regime=Regime.UNKNOWN,
        ),
        decision_at=decision_at,
    )


def _binding(window: WalkForwardWindow, case_id: str) -> WalkForwardReplay:
    return WalkForwardReplay(window, (_replay(case_id, window.eval_start),))


def test_single_window_replay_preserves_inputs_and_evaluates() -> None:
    window = _window()
    cases = (_replay("single", window.eval_start),)
    binding = WalkForwardReplay(window, cases)
    windows = (binding,)
    plan = WalkForwardReplayPlan(windows)

    report = evaluate_walk_forward_replay(plan)

    assert binding.window is window
    assert binding.cases is cases
    assert plan.windows is windows
    assert report == evaluate_regime_cases((cases[0].case,))
    assert report.total == report.passed == 1
    assert report.accuracy == 1.0


@pytest.mark.parametrize("rolling", [False, True], ids=["expanding", "rolling"])
def test_multiple_windows_allow_training_on_previous_evaluation_history(rolling: bool) -> None:
    windows = tuple(
        _binding(_window(2021 + index, 2018 + index if rolling else 2018), f"case-{index}")
        for index in range(3)
    )
    plan = WalkForwardReplayPlan(windows)

    report = evaluate_walk_forward_replay(plan)

    assert windows[1].window.train_end == windows[0].window.eval_end
    assert windows[2].window.train_end == windows[1].window.eval_end
    assert plan.windows is windows
    assert tuple(observation.case_id for observation in report.observations) == (
        "case-0", "case-1", "case-2",
    )
    assert report.total == report.passed == 3


@pytest.mark.parametrize("position", ["start", "middle", "end"])
def test_decisions_inside_closed_interval_are_accepted(position: str) -> None:
    window = _window()
    decision = {
        "start": window.eval_start,
        "middle": window.eval_start + timedelta(days=30),
        "end": window.eval_end,
    }[position]
    replay = _replay("boundary", decision)

    binding = WalkForwardReplay(window, (replay,))

    assert binding.cases[0] is replay
    assert evaluate_walk_forward_replay(WalkForwardReplayPlan((binding,))).passed == 1


@pytest.mark.parametrize("before", [True, False], ids=["before-start", "after-end"])
@pytest.mark.parametrize("index", [0, 1, 2])
def test_outside_decision_is_rejected_at_every_case_position(before: bool, index: int) -> None:
    window = _window()
    outside = (
        window.eval_start - timedelta(microseconds=1)
        if before else window.eval_end + timedelta(microseconds=1)
    )
    cases = tuple(
        _replay(f"case-{item}", outside if item == index else window.eval_start)
        for item in range(3)
    )

    with pytest.raises(ValueError, match=f"case-{index}.*closed evaluation interval"):
        WalkForwardReplay(window, cases)


def test_empty_cases_rejected() -> None:
    with pytest.raises(ValueError, match="cases must not be empty"):
        WalkForwardReplay(_window(), ())


@pytest.mark.parametrize("collection", [list, iter], ids=["list", "iterator"])
def test_case_collection_requires_tuple_without_conversion(collection: type) -> None:
    binding = _binding(_window(), "one")

    with pytest.raises(ValueError, match="cases must be an immutable tuple"):
        replace(binding, cases=collection(binding.cases))


@pytest.mark.parametrize("value", [None, "window", ()])
def test_binding_requires_validated_window(value: object) -> None:
    with pytest.raises(ValueError, match="window must be a WalkForwardWindow"):
        replace(_binding(_window(), "one"), window=value)


@pytest.mark.parametrize("value", [None, "replay", ()])
def test_binding_requires_validated_replay_elements(value: object) -> None:
    binding = _binding(_window(), "one")

    with pytest.raises(ValueError, match="case at index 1 must be a RegimeReplayCase"):
        replace(binding, cases=(*binding.cases, value))


@pytest.mark.parametrize("same_instance", [True, False])
def test_duplicate_case_ids_within_window_rejected(same_instance: bool) -> None:
    window = _window()
    first = _replay("duplicate", window.eval_start)
    second = first if same_instance else _replay("duplicate", window.eval_end)

    with pytest.raises(ValueError, match="duplicate evaluation case_id 'duplicate'"):
        WalkForwardReplay(window, (first, second))


@pytest.mark.parametrize("nonconsecutive", [False, True])
def test_duplicate_case_ids_across_windows_rejected(nonconsecutive: bool) -> None:
    first = _binding(_window(), "duplicate")
    middle = (_binding(_window(2022), "unique"),) if nonconsecutive else ()
    last = _binding(_window(2023), "duplicate")

    with pytest.raises(ValueError, match="duplicate evaluation case_id 'duplicate'"):
        WalkForwardReplayPlan((first, *middle, last))


def test_empty_plan_rejected() -> None:
    with pytest.raises(ValueError, match="windows must not be empty"):
        WalkForwardReplayPlan(())


@pytest.mark.parametrize("collection", [list, iter], ids=["list", "iterator"])
def test_plan_collection_requires_tuple_without_conversion(collection: type) -> None:
    with pytest.raises(ValueError, match="windows must be an immutable tuple"):
        WalkForwardReplayPlan(collection((_binding(_window(), "one"),)))


@pytest.mark.parametrize("value", [None, "binding", ()])
def test_plan_requires_replay_binding_elements(value: object) -> None:
    with pytest.raises(ValueError, match="window at index 1 must be a WalkForwardReplay"):
        WalkForwardReplayPlan((_binding(_window(), "one"), value))  # type: ignore[arg-type]


@pytest.mark.parametrize("violation", ["duplicate", "reversed", "overlap", "touching"])
def test_plan_rejects_existing_temporal_violations_without_sorting(violation: str) -> None:
    first = _window()
    second = _window(2022)
    if violation == "duplicate":
        second = replace(first)
    elif violation == "overlap":
        second = replace(first, eval_start=first.eval_start + timedelta(days=30))
    elif violation == "touching":
        second = replace(first, eval_start=first.eval_end, eval_end=second.eval_end)
    temporal = (second, first) if violation == "reversed" else (first, second)
    windows = tuple(_binding(window, f"case-{index}") for index, window in enumerate(temporal))

    with pytest.raises(ValueError) as existing_error:
        validate_walk_forward_plan(temporal)
    with pytest.raises(ValueError) as plan_error:
        WalkForwardReplayPlan(windows)

    assert str(plan_error.value) == str(existing_error.value)
    assert windows[0].window is temporal[0]
    assert windows[1].window is temporal[1]


def test_plan_rejects_temporal_violation_after_valid_prefix() -> None:
    first, second = _window(), _window(2022)
    third = replace(second, eval_start=second.eval_start + timedelta(days=30))

    with pytest.raises(ValueError, match="evaluation periods"):
        WalkForwardReplayPlan(tuple(
            _binding(window, f"case-{index}") for index, window in enumerate((first, second, third))
        ))


def test_plan_accepts_microsecond_gap_without_imposing_training_order() -> None:
    first = _window(train_start_year=2019)
    second = replace(
        _window(2022, train_start_year=2017),
        train_end=datetime(2019, 12, 31, tzinfo=UTC),
        eval_start=first.eval_end + timedelta(microseconds=1),
    )
    plan = WalkForwardReplayPlan((_binding(first, "first"), _binding(second, "second")))

    assert plan.windows[1].window is second
    assert evaluate_walk_forward_replay(plan).total == 2


@pytest.mark.parametrize("empty", [False, True])
def test_plan_delegates_temporal_validation_and_propagates_error(
    empty: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = () if empty else (_binding(_window(), "one"), _binding(_window(2022), "two"))
    calls: list[tuple[WalkForwardWindow, ...]] = []
    error = ValueError("temporal validator failure")

    def reject(received: tuple[WalkForwardWindow, ...]) -> None:
        calls.append(received)
        raise error

    monkeypatch.setattr(walk_forward_module, "validate_walk_forward_plan", reject)

    with pytest.raises(ValueError) as raised:
        WalkForwardReplayPlan(windows)

    assert raised.value is error
    assert len(calls) == 1
    assert isinstance(calls[0], tuple)
    assert len(calls[0]) == len(windows)
    assert all(value is binding.window for value, binding in zip(calls[0], windows, strict=True))


def test_construction_does_not_evaluate_or_revalidate_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window()
    cases = (_replay("one", window.eval_start),)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction must compose already validated replay cases")

    monkeypatch.setattr(RegimeReplayCase, "__post_init__", unexpected)
    monkeypatch.setattr(walk_forward_module, "evaluate_regime_cases", unexpected)
    monkeypatch.setattr(eval_module, "evaluate_regime_case", unexpected)

    plan = WalkForwardReplayPlan((WalkForwardReplay(window, cases),))

    assert plan.windows[0].cases is cases


def test_binding_and_plan_are_frozen_and_slotted() -> None:
    binding = _binding(_window(), "one")
    plan = WalkForwardReplayPlan((binding,))

    for value, names in ((binding, ("window", "cases")), (plan, ("windows",))):
        assert tuple(field.name for field in fields(value)) == names
        assert not hasattr(value, "__dict__")
        for name in names:
            with pytest.raises(FrozenInstanceError):
                setattr(value, name, None)
            with pytest.raises(FrozenInstanceError):
                delattr(value, name)


def test_runner_preserves_window_case_observation_and_report_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _window(), _window(2022)
    # Deliberately reverse decision time and alphabetical ID order inside a window.
    replays = (
        _replay("z-last", first.eval_end), _replay("a-first", first.eval_start),
        _replay("y-last", second.eval_end), _replay("b-first", second.eval_start),
    )
    windows = (WalkForwardReplay(first, replays[:2]), WalkForwardReplay(second, replays[2:]))
    plan = WalkForwardReplayPlan(windows)
    batch_calls: list[tuple[RegimeEvalCase, ...]] = []
    case_calls: list[RegimeEvalCase] = []
    observations: list[RegimeEvalObservation] = []
    reports: list[RegimeEvalReport] = []
    real_case_evaluator = eval_module.evaluate_regime_case

    def capture_case(case: RegimeEvalCase) -> RegimeEvalObservation:
        case_calls.append(case)
        observation = real_case_evaluator(case)
        observations.append(observation)
        return observation

    def capture_batch(cases: tuple[RegimeEvalCase, ...]) -> RegimeEvalReport:
        batch_calls.append(cases)
        report = evaluate_regime_cases(cases)
        reports.append(report)
        return report

    monkeypatch.setattr(eval_module, "evaluate_regime_case", capture_case)
    monkeypatch.setattr(walk_forward_module, "evaluate_regime_cases", capture_batch)

    report = evaluate_walk_forward_replay(plan)

    assert plan.windows is windows
    assert len(batch_calls) == len(reports) == 1
    assert isinstance(batch_calls[0], tuple)
    assert len(case_calls) == len(replays)
    assert all(case is replay.case for case, replay in zip(batch_calls[0], replays, strict=True))
    assert all(case is replay.case for case, replay in zip(case_calls, replays, strict=True))
    assert tuple(observation.case_id for observation in report.observations) == (
        "z-last", "a-first", "y-last", "b-first",
    )
    assert all(a is b for a, b in zip(report.observations, observations, strict=True))
    assert report is reports[0]


def test_real_historical_cases_reuse_report_accuracy_confusion_and_tag_semantics() -> None:
    benchmark = build_regime_benchmark_v1()
    decision = benchmark.cases[0].cutoff_at
    first = replace(
        _window(2026), eval_start=decision, eval_end=decision + timedelta(minutes=10),
    )
    second = replace(
        first, eval_start=decision + timedelta(minutes=11),
        eval_end=decision + timedelta(minutes=30),
    )
    cases = tuple(
        replace(case, cutoff_at=decision if index < 2 else second.eval_start)
        for index, case in enumerate(benchmark.cases)
    )
    cases = (*cases[:3], replace(cases[3], expected_regime=Regime.PERMISSIVE))
    replays = tuple(RegimeReplayCase(case, case.cutoff_at) for case in cases)
    plan = WalkForwardReplayPlan((
        WalkForwardReplay(first, replays[:2]), WalkForwardReplay(second, replays[2:]),
    ))

    report = evaluate_walk_forward_replay(plan)
    direct = evaluate_regime_cases(cases)

    assert type(report) is RegimeEvalReport
    assert report == direct == evaluate_walk_forward_replay(plan)
    assert report.total == 4
    assert report.passed == 3
    assert report.failed == 1
    assert report.accuracy == direct.accuracy == 0.75
    assert report.confusion_counts == direct.confusion_counts
    assert tuple(report.confusion_counts) == tuple((a, b) for a in Regime for b in Regime)
    assert report.confusion_counts[(Regime.PERMISSIVE, Regime.UNKNOWN)] == 1
    assert report.confusion_counts[(Regime.UNKNOWN, Regime.PERMISSIVE)] == 0
    assert report.tag_summaries == (
        TagEvalSummary("missing_data", 1, 0),
        TagEvalSummary("normal", 2, 2),
        TagEvalSummary("stress", 1, 1),
    )


@pytest.mark.parametrize("error_type", [RuntimeError, InvalidMarketDataError])
def test_runner_propagates_errors_and_stops_remaining_cases(
    error_type: type[Exception], monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _window(), _window(2022)
    plan = WalkForwardReplayPlan((
        _binding(first, "first"),
        WalkForwardReplay(second, (
            _replay("broken", second.eval_start), _replay("not-reached", second.eval_end),
        )),
    ))
    calls: list[str] = []
    error = error_type("case evaluation failure")
    real_evaluator = eval_module.evaluate_regime_case

    def fail_case(case: RegimeEvalCase) -> RegimeEvalObservation:
        calls.append(case.case_id)
        if case.case_id == "broken":
            raise error
        return real_evaluator(case)

    monkeypatch.setattr(eval_module, "evaluate_regime_case", fail_case)

    with pytest.raises(error_type) as raised:
        evaluate_walk_forward_replay(plan)

    assert raised.value is error
    assert calls == ["first", "broken"]


def test_actual_workflow_error_propagates_through_validated_plan() -> None:
    case = build_regime_benchmark_v1().cases[0]
    duplicate = replace(case, outcomes=(case.outcomes[0], case.outcomes[0]))
    replay = RegimeReplayCase(duplicate, duplicate.cutoff_at)
    plan = WalkForwardReplayPlan((WalkForwardReplay(_window(2026), (replay,)),))

    with pytest.raises(ValueError, match="duplicate HistoricalBarsOutcome"):
        evaluate_walk_forward_replay(plan)


@pytest.mark.parametrize("value", [None, (), "plan"])
def test_runner_requires_validated_plan(value: object) -> None:
    with pytest.raises(ValueError, match="plan must be a WalkForwardReplayPlan"):
        evaluate_walk_forward_replay(value)  # type: ignore[arg-type]


def test_orchestration_has_no_clock_dependency() -> None:
    tree = ast.parse(Path(walk_forward_module.__file__).read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name not in {"datetime", "time"} for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in {"datetime", "time"}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"now", "utcnow", "today", "time"}
