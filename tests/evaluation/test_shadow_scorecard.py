from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from finance_research_agent.domain.models import PublishedRunBundle, RunContext
from finance_research_agent.evaluation.shadow_scorecard import (
    DailyUsefulnessObservation,
    EvaluationGate,
    EvaluationGateStatus,
    ShadowEvaluationOrigin,
    ShadowGateId,
    ShadowScorecard,
    build_shadow_scorecard,
)


class _Calendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date.weekday() < 5

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        raise NotImplementedError


def _gates(status: EvaluationGateStatus = EvaluationGateStatus.PENDING):
    return tuple(
        EvaluationGate(
            id=gate_id,
            status=status,
            reason_codes=("evidence-pending",) if status is EvaluationGateStatus.PENDING else (),
        )
        for gate_id in ShadowGateId
    )


def _scorecard(**changes: object) -> ShadowScorecard:
    values: dict[str, object] = {"gate_results": _gates()}
    values.update(changes)
    return ShadowScorecard(**values)


def test_legacy_scorecard_call_cannot_claim_unverified_gates() -> None:
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=None)

    assert tuple(gate.id for gate in scorecard.gate_results) == tuple(ShadowGateId)
    assert {gate.status for gate in scorecard.gate_results} == {"PENDING"}
    assert all(
        "VERIFIED_SHADOW_EVIDENCE_REQUIRED" in gate.reason_codes
        for gate in scorecard.gate_results
    )
    assert scorecard.failed_gate_ids == ()
    assert scorecard.pending_gate_ids == tuple(ShadowGateId)
    assert scorecard.graduation_ready is False


def test_scorecard_contract_excludes_performance_and_execution_fields() -> None:
    forbidden = {
        "realized_profit_loss",
        "return_pct",
        "win_rate",
        "fills",
        "fees",
        "slippage",
        "executed_units",
    }

    assert set(ShadowScorecard.model_fields).isdisjoint(forbidden)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _scorecard(realized_profit_loss="not a scorecard field")


def test_pending_gate_requires_a_reason_code() -> None:
    with pytest.raises(ValidationError, match="requires a reason code"):
        EvaluationGate(id=ShadowGateId.SAFETY, status=EvaluationGateStatus.PENDING)


def test_usefulness_observation_requires_score_or_corrective_work() -> None:
    with pytest.raises(ValidationError, match="requires a score or corrective work"):
        DailyUsefulnessObservation(market_date=date(2026, 8, 26), feedback_id="feedback-1")

    scored = DailyUsefulnessObservation(
        market_date=date(2026, 8, 26), feedback_id="feedback-2", usefulness_score=4
    )
    corrective = DailyUsefulnessObservation(
        market_date=date(2026, 8, 26),
        feedback_id="feedback-3",
        corrective_work="Review the opening-range explanation.",
    )
    assert scored.usefulness_score == 4
    assert corrective.corrective_work == "Review the opening-range explanation."


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"period_start": date(2026, 8, 26)}, "requires both start and end"),
        (
            {"period_start": date(2026, 8, 27), "period_end": date(2026, 8, 26)},
            "must not follow",
        ),
        ({"gate_results": _gates()[:-1]}, "every shadow gate exactly once in order"),
        ({"valid_trading_days": 1}, "cannot exceed eligible_online_days"),
        ({"published_in_target_window": 1}, "cannot exceed eligible_online_days"),
    ),
)
def test_scorecard_rejects_inconsistent_period_gate_order_and_day_counts(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _scorecard(**changes)


def test_scorecard_derives_failure_and_pending_ids_without_marking_ready() -> None:
    gates = list(_gates(EvaluationGateStatus.PASS))
    gates[0] = EvaluationGate(
        id=ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS,
        status=EvaluationGateStatus.FAIL,
    )
    gates[1] = EvaluationGate(
        id=ShadowGateId.SAFETY,
        status=EvaluationGateStatus.PENDING,
        reason_codes=("missing-incident-evidence",),
    )
    scorecard = _scorecard(
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        valid_trading_days=20,
        eligible_online_days=20,
        gate_results=tuple(gates),
    )

    assert scorecard.failed_gate_ids == (ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS,)
    assert scorecard.pending_gate_ids == (ShadowGateId.SAFETY,)
    assert scorecard.graduation_ready is False


def test_only_twenty_complete_personal_shadow_days_can_be_ready() -> None:
    complete = _scorecard(
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        valid_trading_days=20,
        eligible_online_days=20,
        gate_results=_gates(EvaluationGateStatus.PASS),
    )
    short = _scorecard(
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        valid_trading_days=19,
        eligible_online_days=20,
        gate_results=_gates(EvaluationGateStatus.PASS),
    )
    synthetic = _scorecard(
        origin=ShadowEvaluationOrigin.SYNTHETIC_EVALUATION,
        valid_trading_days=20,
        eligible_online_days=20,
        gate_results=_gates(EvaluationGateStatus.PASS),
    )

    assert complete.graduation_ready is True
    assert short.graduation_ready is False
    assert synthetic.graduation_ready is False


def test_legacy_scorecard_period_uses_sorted_unique_published_dates(valid_packet) -> None:
    base_run = valid_packet.run

    def bundle_for(market_date: date) -> PublishedRunBundle:
        values = base_run.model_dump(mode="python")
        values["market_date"] = market_date
        values["run_id"] = f"premarket-{market_date.isoformat()}-r1"
        return PublishedRunBundle(run=RunContext.model_validate(values))

    scorecard = build_shadow_scorecard(
        (
            bundle_for(date(2026, 8, 27)),
            bundle_for(date(2026, 8, 26)),
            bundle_for(date(2026, 8, 27)),
        ),
        (),
        _Calendar(),
    )

    assert scorecard.period_start == date(2026, 8, 26)
    assert scorecard.period_end == date(2026, 8, 27)
    assert scorecard.valid_trading_days == 0
