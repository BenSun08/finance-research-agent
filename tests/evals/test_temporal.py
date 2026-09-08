from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from finance_research_agent.evals import WalkForwardWindow, validate_walk_forward_plan

TIMESTAMP_FIELDS = ("train_start", "train_end", "eval_start", "eval_end")


def _window(train_start_year: int = 2018, eval_year: int = 2021) -> WalkForwardWindow:
    return WalkForwardWindow(
        train_start=datetime(train_start_year, 1, 1, tzinfo=UTC),
        train_end=datetime(eval_year - 1, 12, 31, 23, 59, 59, tzinfo=UTC),
        eval_start=datetime(eval_year, 1, 1, tzinfo=UTC),
        eval_end=datetime(eval_year, 12, 31, 23, 59, 59, tzinfo=UTC),
    )


def test_valid_single_window() -> None:
    window = _window()

    assert window.train_start < window.train_end < window.eval_start < window.eval_end
    assert validate_walk_forward_plan((window,)) is None


def test_valid_expanding_plan_can_train_on_previous_evaluation_history() -> None:
    first = _window()
    second = _window(eval_year=2022)
    third = _window(eval_year=2023)

    assert first.train_start == second.train_start == third.train_start
    assert second.train_end == first.eval_end
    assert third.train_end == second.eval_end
    assert validate_walk_forward_plan((first, second, third)) is None


def test_valid_rolling_plan_can_train_on_previous_evaluation_history() -> None:
    first = _window()
    second = _window(train_start_year=2019, eval_year=2022)
    third = _window(train_start_year=2020, eval_year=2023)

    assert first.train_start < second.train_start < third.train_start
    assert second.train_end == first.eval_end
    assert third.train_end == second.eval_end
    assert validate_walk_forward_plan((first, second, third)) is None


def test_plan_preserves_caller_order_without_imposing_a_training_strategy() -> None:
    first = _window(train_start_year=2019)
    second = replace(
        _window(train_start_year=2017, eval_year=2022),
        train_end=datetime(2019, 12, 31, tzinfo=UTC),
    )
    windows = (first, second)

    assert second.train_start < first.train_start
    assert second.train_end < first.train_end
    assert validate_walk_forward_plan(windows) is None
    assert windows[0] is first
    assert windows[1] is second


@pytest.mark.parametrize("field_name", TIMESTAMP_FIELDS)
def test_naive_datetime_rejected(field_name: str) -> None:
    window = _window()
    naive = getattr(window, field_name).replace(tzinfo=None)

    with pytest.raises(ValueError, match=f"{field_name} must be timezone-aware UTC"):
        replace(window, **{field_name: naive})


@pytest.mark.parametrize("field_name", TIMESTAMP_FIELDS)
@pytest.mark.parametrize("offset_hours", [-5, 8])
def test_nonzero_utc_offset_rejected_without_conversion(
    field_name: str, offset_hours: int
) -> None:
    window = _window()
    same_instant = getattr(window, field_name).astimezone(timezone(timedelta(hours=offset_hours)))

    with pytest.raises(ValueError, match=f"{field_name} must be timezone-aware UTC"):
        replace(window, **{field_name: same_instant})


@pytest.mark.parametrize("field_name", TIMESTAMP_FIELDS)
def test_zero_offset_timezone_accepted_and_preserved(field_name: str) -> None:
    window = _window()
    zone = timezone(timedelta(0), "UTC-alias")
    timestamp = getattr(window, field_name).replace(tzinfo=zone)

    result = replace(window, **{field_name: timestamp})

    assert zone is not UTC
    assert getattr(result, field_name) is timestamp
    assert validate_walk_forward_plan((result,)) is None


@pytest.mark.parametrize("field_name", TIMESTAMP_FIELDS)
@pytest.mark.parametrize("value", [None, date(2020, 1, 1), "2020-01-01T00:00:00Z"])
def test_non_datetime_values_rejected(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=f"{field_name} must be timezone-aware UTC"):
        replace(_window(), **{field_name: value})


@pytest.mark.parametrize(
    ("earlier_field", "later_field"),
    [("train_start", "train_end"), ("train_end", "eval_start"), ("eval_start", "eval_end")],
)
@pytest.mark.parametrize("delta", [timedelta(0), timedelta(microseconds=1)])
def test_window_rejects_equal_or_reversed_boundaries(
    earlier_field: str, later_field: str, delta: timedelta
) -> None:
    window = _window()

    with pytest.raises(ValueError, match=f"{earlier_field} must be before {later_field}"):
        replace(window, **{earlier_field: getattr(window, later_field) + delta})


def test_empty_plan_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_walk_forward_plan(())


def test_mutable_list_rejected_without_conversion() -> None:
    windows = [_window()]

    with pytest.raises(ValueError, match="immutable tuple"):
        validate_walk_forward_plan(windows)  # type: ignore[arg-type]


def test_iterator_rejected_without_materialization() -> None:
    windows = iter((_window(),))

    with pytest.raises(ValueError, match="immutable tuple"):
        validate_walk_forward_plan(windows)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, "window", ()])
def test_plan_rejects_non_window_elements(value: object) -> None:
    with pytest.raises(ValueError, match="WalkForwardWindow"):
        validate_walk_forward_plan((_window(), value))  # type: ignore[arg-type]


@pytest.mark.parametrize("copy", [False, True], ids=["same-instance", "equal-value"])
def test_duplicate_windows_rejected(copy: bool) -> None:
    window = _window()
    duplicate = replace(window) if copy else window

    with pytest.raises(ValueError, match="duplicate"):
        validate_walk_forward_plan((window, duplicate))


def test_nonconsecutive_duplicate_windows_rejected() -> None:
    window = _window()

    with pytest.raises(ValueError, match="duplicate"):
        validate_walk_forward_plan((window, _window(eval_year=2022), replace(window)))


def test_reversed_evaluation_order_rejected_without_sorting() -> None:
    first = _window()
    second = _window(eval_year=2022)
    windows = (second, first)

    with pytest.raises(ValueError, match="evaluation periods"):
        validate_walk_forward_plan(windows)

    assert windows[0] is second
    assert windows[1] is first


@pytest.mark.parametrize(
    ("start_month", "end_year"), [(6, 2022), (6, 2021), (1, 2022)],
    ids=["partial-overlap", "contained-period", "same-start"],
)
def test_overlapping_evaluation_periods_rejected(start_month: int, end_year: int) -> None:
    first = _window()
    second = replace(
        first,
        eval_start=datetime(2021, start_month, 1, tzinfo=UTC),
        eval_end=datetime(end_year, 7, 1, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="evaluation periods"):
        validate_walk_forward_plan((first, second))


def test_touching_evaluation_periods_rejected() -> None:
    first = _window()
    second = replace(
        first,
        eval_start=first.eval_end,
        eval_end=datetime(2022, 12, 31, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="evaluation periods"):
        validate_walk_forward_plan((first, second))


def test_plan_checks_evaluation_separation_after_a_valid_prefix() -> None:
    first = _window()
    second = _window(eval_year=2022)
    third = replace(
        _window(eval_year=2023),
        train_end=datetime(2022, 5, 31, tzinfo=UTC),
        eval_start=datetime(2022, 6, 1, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="evaluation periods"):
        validate_walk_forward_plan((first, second, third))


def test_strict_gap_of_one_microsecond_is_valid() -> None:
    first = _window()
    second = replace(
        first,
        eval_start=first.eval_end + timedelta(microseconds=1),
        eval_end=datetime(2022, 12, 31, tzinfo=UTC),
    )

    assert validate_walk_forward_plan((first, second)) is None


@pytest.mark.parametrize("field_name", TIMESTAMP_FIELDS)
def test_window_is_immutable(field_name: str) -> None:
    window = _window()

    with pytest.raises(FrozenInstanceError):
        setattr(window, field_name, datetime(2000, 1, 1, tzinfo=UTC))
    with pytest.raises(FrozenInstanceError):
        delattr(window, field_name)
    assert not hasattr(window, "__dict__")
