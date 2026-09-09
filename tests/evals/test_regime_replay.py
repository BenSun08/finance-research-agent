import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import finance_research_agent.evals.regime as eval_module
import finance_research_agent.evals.regime_replay as replay_module
from finance_research_agent.domain.market import DailyBar, InvalidMarketDataError
from finance_research_agent.domain.regime import Regime, RegimePolicy
from finance_research_agent.evals import RegimeEvalCase, RegimeReplayCase, evaluate_regime_case
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

DECISION_AT = datetime(2026, 8, 25, 13, tzinfo=UTC)
EVIDENCE_CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
LATE_RETRIEVAL = datetime(2026, 9, 9, 12, tzinfo=UTC)
SESSION = date(2026, 8, 24)


def _outcome(
    symbol: str = "SPY",
    *,
    failure: bool = False,
    evidence_cutoff_at: datetime = EVIDENCE_CUTOFF,
    retrieved_at: datetime = LATE_RETRIEVAL,
) -> HistoricalBarsOutcome:
    provenance = HistoricalBarsProvenance(
        provider="synthetic",
        feed=MarketDataFeed.IEX,
        coverage=MarketDataCoverage.SINGLE_EXCHANGE,
        adjustment=BarAdjustment.RAW,
        requested_start_at=datetime(2026, 8, 24, 4, tzinfo=UTC),
        requested_end_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
        retrieved_at=retrieved_at,
        evidence_cutoff_at=evidence_cutoff_at,
        completed_through_session=SESSION,
        adapter_version="synthetic-v1",
    )
    if failure:
        return HistoricalBarsFailure(
            schema_version=FAILURE_SCHEMA_VERSION, symbol=symbol,
            reason=HistoricalBarsUnavailableReason.NO_DATA, provenance=provenance,
            missing_sessions=(SESSION,), quality_flags=("SYNTHETIC",),
        )
    return HistoricalDailyBars.create(
        symbol=symbol,
        observations=(DailyBarObservation(
            source_timestamp=datetime(2026, 8, 24, 21, tzinfo=UTC),
            bar=DailyBar(
                session_date=SESSION, open=Decimal("100"), high=Decimal("102"),
                low=Decimal("99"), close=Decimal("101"), volume=1_000_000,
            ),
        ),),
        provenance=provenance,
        quality_flags=("SYNTHETIC",),
    )


def _case(outcomes: tuple[HistoricalBarsOutcome, ...] = ()) -> RegimeEvalCase:
    return RegimeEvalCase(
        case_id="replay-case", outcomes=outcomes, policy=RegimePolicy(),
        cutoff_at=DECISION_AT, expected_regime=Regime.UNKNOWN,
    )


@pytest.mark.parametrize("failure", [False, True], ids=["available", "failure"])
@pytest.mark.parametrize("cutoff", [EVIDENCE_CUTOFF, DECISION_AT], ids=["earlier", "equal"])
def test_replay_accepts_evidence_by_decision_and_preserves_inputs(
    failure: bool, cutoff: datetime
) -> None:
    outcome = _outcome(failure=failure, evidence_cutoff_at=cutoff)
    case = _case((outcome,))

    replay = RegimeReplayCase(case=case, decision_at=DECISION_AT)

    assert replay.case is case
    assert replay.case.outcomes is case.outcomes
    assert replay.case.outcomes[0] is outcome
    assert replay.decision_at is DECISION_AT
    assert evaluate_regime_case(replay.case) == evaluate_regime_case(case)


@pytest.mark.parametrize("failure", [False, True], ids=["available", "failure"])
@pytest.mark.parametrize("position", [0, 1, 2], ids=["first", "second", "unused-symbol"])
def test_future_evidence_is_rejected_in_every_outcome_position(
    failure: bool, position: int
) -> None:
    symbols = ("SPY", "QQQ", "UNUSED")
    outcomes = tuple(
        _outcome(
            symbol, failure=failure,
            evidence_cutoff_at=(
                DECISION_AT + timedelta(hours=1) if index == position else EVIDENCE_CUTOFF
            ),
        )
        for index, symbol in enumerate(symbols)
    )

    with pytest.raises(ValueError, match=f"{symbols[position]}.*evidence_cutoff_at.*decision_at"):
        RegimeReplayCase(case=_case(outcomes), decision_at=DECISION_AT)


@pytest.mark.parametrize("failure", [False, True], ids=["available", "failure"])
@pytest.mark.parametrize(
    "retrieved_at", [EVIDENCE_CUTOFF, DECISION_AT, LATE_RETRIEVAL],
    ids=["before-decision", "at-decision", "after-decision"],
)
def test_retrieval_time_is_independent_of_simulated_decision(
    failure: bool, retrieved_at: datetime
) -> None:
    replay = RegimeReplayCase(
        case=_case((_outcome(failure=failure, retrieved_at=retrieved_at),)),
        decision_at=DECISION_AT,
    )

    assert replay.case.outcomes[0].provenance.retrieved_at is retrieved_at
    assert replay.case.outcomes[0].provenance.evidence_cutoff_at == EVIDENCE_CUTOFF
    assert replay.case.cutoff_at == replay.decision_at == DECISION_AT


def test_decision_time_is_required_even_when_case_and_retrieval_have_timestamps() -> None:
    with pytest.raises(TypeError, match="decision_at"):
        RegimeReplayCase(case=_case((_outcome(),)))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "decision_at",
    [
        DECISION_AT.replace(tzinfo=None),
        DECISION_AT.astimezone(timezone(timedelta(hours=8))),
        DECISION_AT.astimezone(timezone(timedelta(hours=-5))),
        None, date(2026, 8, 25), "2026-08-25T13:00:00Z",
    ],
)
def test_decision_time_requires_utc_datetime_without_conversion(decision_at: object) -> None:
    with pytest.raises(ValueError, match="decision_at must be timezone-aware UTC"):
        RegimeReplayCase(case=_case(), decision_at=decision_at)  # type: ignore[arg-type]


def test_zero_offset_timezone_is_accepted_without_replacing_the_timestamp() -> None:
    decision_at = DECISION_AT.replace(tzinfo=timezone(timedelta(0), "UTC-alias"))

    replay = RegimeReplayCase(case=_case(), decision_at=decision_at)

    assert replay.decision_at is decision_at
    assert replay.decision_at == replay.case.cutoff_at


@pytest.mark.parametrize("delta", [timedelta(microseconds=-1), timedelta(microseconds=1)])
def test_evaluator_cutoff_must_equal_decision_time(delta: timedelta) -> None:
    case = replace(_case(), cutoff_at=DECISION_AT + delta)

    with pytest.raises(ValueError, match="case.cutoff_at must equal decision_at"):
        RegimeReplayCase(case=case, decision_at=DECISION_AT)


@pytest.mark.parametrize(
    "cutoff_at",
    [DECISION_AT.replace(tzinfo=None), DECISION_AT.astimezone(timezone(timedelta(hours=8)))],
)
def test_wrapped_case_cutoff_must_also_follow_utc_convention(cutoff_at: datetime) -> None:
    with pytest.raises(ValueError, match="case.cutoff_at must be timezone-aware UTC"):
        RegimeReplayCase(case=replace(_case(), cutoff_at=cutoff_at), decision_at=DECISION_AT)


def test_empty_outcomes_retain_existing_unknown_evaluation_behavior() -> None:
    replay = RegimeReplayCase(case=_case(), decision_at=DECISION_AT)

    assert evaluate_regime_case(replay.case).actual_regime is Regime.UNKNOWN


def test_replay_and_wrapped_case_are_immutable() -> None:
    replay = RegimeReplayCase(case=_case((_outcome(),)), decision_at=DECISION_AT)

    assert tuple(field.name for field in fields(replay)) == ("case", "decision_at")
    assert not hasattr(replay, "__dict__")
    for field_name in ("case", "decision_at"):
        with pytest.raises(FrozenInstanceError):
            setattr(replay, field_name, None)
        with pytest.raises(FrozenInstanceError):
            delattr(replay, field_name)
    with pytest.raises(FrozenInstanceError):
        replay.case.cutoff_at = EVIDENCE_CUTOFF  # type: ignore[misc]


def test_replay_requires_a_regime_eval_case() -> None:
    with pytest.raises(ValueError, match="RegimeEvalCase"):
        RegimeReplayCase(case=object(), decision_at=DECISION_AT)  # type: ignore[arg-type]


def test_replay_rejects_unrecognized_outcomes() -> None:
    case = _case((_outcome(), object()))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="HistoricalBarsOutcome"):
        RegimeReplayCase(case=case, decision_at=DECISION_AT)


def test_replay_requires_validated_provenance() -> None:
    outcome = replace(_outcome(failure=True), provenance=object())

    with pytest.raises(ValueError, match="HistoricalBarsProvenance"):
        RegimeReplayCase(case=_case((outcome,)), decision_at=DECISION_AT)


@pytest.mark.parametrize("error_type", [RuntimeError, InvalidMarketDataError])
def test_existing_evaluator_errors_propagate_unchanged(
    error_type: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = RegimeReplayCase(case=_case((_outcome(),)), decision_at=DECISION_AT)
    error = error_type("evaluation failure")

    def fail_workflow(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(eval_module, "run_regime_workflow", fail_workflow)
    with pytest.raises(error_type) as raised:
        evaluate_regime_case(replay.case)

    assert raised.value is error


def test_duplicate_symbol_error_still_belongs_to_existing_workflow() -> None:
    outcome = _outcome()
    replay = RegimeReplayCase(case=_case((outcome, outcome)), decision_at=DECISION_AT)

    with pytest.raises(ValueError, match="duplicate HistoricalBarsOutcome"):
        evaluate_regime_case(replay.case)


def test_replay_contract_has_no_clock_calls() -> None:
    tree = ast.parse(Path(replay_module.__file__).read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"now", "utcnow", "today", "time"}
