from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from finance_research_agent.domain.models import (
    PublishedArtifact,
    PublishedRunBundle,
    RunContext,
    StoredRun,
)
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    ShadowDayAvailability,
    ShadowDayExclusionReason,
    ShadowDayObservation,
)
from finance_research_agent.evaluation.shadow_scorecard import (
    DailyUsefulnessObservation,
    EvaluationGate,
    EvaluationGateStatus,
    ShadowEvaluationOrigin,
    ShadowGateId,
    ShadowPeriod,
    ShadowScorecard,
    build_shadow_scorecard,
)
from tests.support.shadow_evidence import (
    personal_reduced_publication,
    recorded_component_versions,
)


class _Calendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date.weekday() < 5

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        raise NotImplementedError


class _EmptyRuns:
    def __init__(self, records: tuple[StoredRun, ...] = ()) -> None:
        self.records = records

    def list_runs(self, market_date_start: date, market_date_end: date) -> tuple[()]:
        return tuple(
            item
            for item in self.records
            if market_date_start <= item.run.market_date <= market_date_end
        )

    def load(self, run_id: str):
        return next((item for item in self.records if item.run.run_id == run_id), None)

    def read_staged_artifact(self, run_id: str, artifact_name: str) -> bytes | None:
        return None


class _EmptyArtifacts:
    def __init__(self) -> None:
        self.bundle: PublishedRunBundle | None = None
        self.report: str | None = None
        self.artifact: PublishedArtifact | None = None

    def load_published_bundle(self, run_id: str):
        return self.bundle

    def get_report(self, run_id: str) -> None:
        return self.report

    def get_published_artifact(self, run_id: str) -> None:
        return self.artifact


class _EmptyFeedback:
    def list_feedback(self) -> tuple[()]:
        return ()


class _EmptyObservations:
    def __init__(self, records: tuple[ShadowDayObservation, ...]) -> None:
        self.records = records

    def list_observations(self) -> tuple[ShadowDayObservation, ...]:
        return self.records


class _EmptyAttestations:
    def list_attestations(self) -> tuple[()]:
        return ()


class _EmptyReleaseEvidence:
    def list_receipts(self) -> tuple[()]:
        return ()


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


def test_shadow_period_uses_inclusive_ordered_date_bounds() -> None:
    period = ShadowPeriod(
        start_date=date(2026, 8, 26),
        end_date=date(2026, 8, 28),
    )

    assert period.start_date == date(2026, 8, 26)
    assert period.end_date == date(2026, 8, 28)


def test_shadow_period_rejects_reversed_date_bounds() -> None:
    with pytest.raises(ValidationError, match="start_date must not follow end_date"):
        ShadowPeriod(
            start_date=date(2026, 8, 28),
            end_date=date(2026, 8, 26),
        )


def test_legacy_scorecard_call_cannot_claim_unverified_gates() -> None:
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=None)

    assert tuple(gate.id for gate in scorecard.gate_results) == tuple(ShadowGateId)
    assert {gate.status for gate in scorecard.gate_results} == {"PENDING"}
    assert all(
        "VERIFIED_SHADOW_EVIDENCE_REQUIRED" in gate.reason_codes for gate in scorecard.gate_results
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


def test_verified_scorecard_counts_eligible_days_and_keeps_unknown_coverage_pending() -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 7))
    observations = (
        ShadowDayObservation(
            id="online-monday",
            market_date=date(2026, 10, 5),
            recorded_at=datetime(2026, 10, 5, 16, tzinfo=UTC),
            session_id="session-monday",
            availability=ShadowDayAvailability.ONLINE,
        ),
        ShadowDayObservation(
            id="excluded-tuesday",
            market_date=date(2026, 10, 6),
            recorded_at=datetime(2026, 10, 6, 16, tzinfo=UTC),
            session_id="session-tuesday",
            availability=ShadowDayAvailability.EXCLUDED,
            exclusion_reason=ShadowDayExclusionReason.COMPUTER_OR_CODEX_UNAVAILABLE,
        ),
        ShadowDayObservation(
            id="unknown-wednesday",
            market_date=date(2026, 10, 7),
            recorded_at=datetime(2026, 10, 7, 16, tzinfo=UTC),
            session_id="session-wednesday",
            availability=ShadowDayAvailability.UNKNOWN,
        ),
    )
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations(observations),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.period_start == period.start_date
    assert scorecard.period_end == period.end_date
    assert scorecard.eligible_online_days == 2
    assert scorecard.valid_trading_days == 0
    assert scorecard.published_in_target_window == 0
    gates = {gate.id: gate for gate in scorecard.gate_results}
    assert gates[ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS].status is EvaluationGateStatus.PENDING
    assert gates[ShadowGateId.TIMELY_PUBLICATION].status is EvaluationGateStatus.PENDING
    timely_reasons = gates[ShadowGateId.TIMELY_PUBLICATION].reason_codes
    assert "UNKNOWN_OR_MISSING_DAY_OBSERVATION" in timely_reasons


def test_personal_research_publication_counts_once_inside_new_york_target_window(
    valid_packet, tmp_path, monkeypatch
) -> None:
    from finance_research_agent.application import shadow_evidence_loader
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    bundle, artifact = personal_reduced_publication(valid_packet, tmp_path)
    artifact = artifact.model_copy(
        update={"published_at": artifact.published_at + timedelta(minutes=13)}
    )
    monkeypatch.setattr(
        shadow_evidence_loader,
        "current_component_versions",
        lambda snapshot: recorded_component_versions(bundle.run),
    )
    period = ShadowPeriod(start_date=bundle.run.market_date, end_date=bundle.run.market_date)
    observation = ShadowDayObservation(
        id="online-publication-day",
        market_date=bundle.run.market_date,
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        session_id="session-publication-day",
        availability=ShadowDayAvailability.ONLINE,
    )
    runs = _EmptyRuns((StoredRun(run=bundle.run, published=True),))
    artifacts = _EmptyArtifacts()
    artifacts.bundle = bundle
    artifacts.report = bundle.report_markdown
    artifacts.artifact = artifact
    evidence = load_shadow_evaluation_evidence(
        period,
        runs,  # type: ignore[arg-type]
        artifacts,  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations((observation,)),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert artifact.published_at.astimezone(ZoneInfo("America/New_York")).time() == time(9, 0)
    assert scorecard.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW
    assert scorecard.eligible_online_days == 1
    assert scorecard.valid_trading_days == 1
    assert scorecard.published_in_target_window == 1
    timely_gate = next(
        gate for gate in scorecard.gate_results if gate.id is ShadowGateId.TIMELY_PUBLICATION
    )
    assert timely_gate.status is EvaluationGateStatus.PASS
    assert timely_gate.measured_value == 1


def test_verified_scorecard_can_end_on_last_representable_calendar_date() -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date.max, end_date=date.max)
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations(()),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.period_end == date.max
    assert scorecard.eligible_online_days == 1


def test_verified_scorecard_uses_latest_hash_linked_exclusion_correction() -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 5))
    original = ShadowDayObservation(
        id="online-before-correction",
        market_date=date(2026, 10, 5),
        recorded_at=datetime(2026, 10, 5, 15, tzinfo=UTC),
        session_id="session-corrected",
        availability=ShadowDayAvailability.ONLINE,
    )
    correction = ShadowDayObservation(
        id="exclusion-correction",
        market_date=date(2026, 10, 5),
        recorded_at=datetime(2026, 10, 5, 16, tzinfo=UTC),
        session_id="session-corrected",
        availability=ShadowDayAvailability.EXCLUDED,
        exclusion_reason=ShadowDayExclusionReason.TOTAL_LOCAL_NETWORK_OUTAGE,
        corrects_observation_id=original.id,
        corrects_observation_sha256=sha256(canonical_bytes(original)).hexdigest(),
    )
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations((original, correction)),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.eligible_online_days == 0
    timely_gate = next(
        gate for gate in scorecard.gate_results if gate.id is ShadowGateId.TIMELY_PUBLICATION
    )
    assert timely_gate.status is EvaluationGateStatus.PENDING
    assert timely_gate.reason_codes == ("NO_ELIGIBLE_TRADING_DAYS",)
