from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from finance_research_agent.application.shadow_evidence_service import (
    OperatorReleaseAttestationReceipt,
    RecordOperatorReleaseAttestation,
    RecordShadowDayObservation,
    ShadowObservationReceipt,
    record_operator_release_attestation,
    record_shadow_day_observation,
)
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    OperatorReleaseCheckId,
    OperatorReleaseResult,
    ShadowDayAvailability,
    ShadowDayObservation,
    ShadowSafetyIncidentCategory,
    ShadowSafetyIncidentObservation,
)


class _Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now_utc(self) -> datetime:
        return self.current


class _Calendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date.weekday() < 5

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        raise NotImplementedError


class _Observations:
    def __init__(self) -> None:
        self.items: dict[str, ShadowDayObservation] = {}
        self.run_ids = {"premarket-2026-10-09-r1"}
        self.feedback_ids = {"feedback-1"}

    def has_run_id(self, run_id: str) -> bool:
        return run_id in self.run_ids

    def has_feedback_id(self, feedback_id: str) -> bool:
        return feedback_id in self.feedback_ids

    def get_observation(self, observation_id: str) -> ShadowDayObservation | None:
        return self.items.get(observation_id)

    def append_observation(self, observation: ShadowDayObservation) -> None:
        if observation.id in self.items:
            raise ValueError("observation id already exists")
        self.items[observation.id] = observation

    def list_observations(self) -> tuple[ShadowDayObservation, ...]:
        return tuple(self.items.values())


class _Attestations:
    def __init__(self) -> None:
        self.items: dict[str, OperatorReleaseAttestation] = {}

    def append_attestation(self, attestation: OperatorReleaseAttestation) -> None:
        if attestation.id in self.items:
            raise ValueError("attestation id already exists")
        self.items[attestation.id] = attestation

    def list_attestations(self) -> tuple[OperatorReleaseAttestation, ...]:
        return tuple(self.items.values())


def _day_request(**changes: object) -> RecordShadowDayObservation:
    values: dict[str, object] = {
        "id": "shadow-day-1",
        "market_date": date(2026, 10, 9),
        "session_id": "session-2026-10-09",
        "availability": ShadowDayAvailability.ONLINE,
    }
    values.update(changes)
    return RecordShadowDayObservation(**values)


def _attestation_request(**changes: object) -> RecordOperatorReleaseAttestation:
    values: dict[str, object] = {
        "id": "attestation-desktop-1",
        "check_id": OperatorReleaseCheckId.DESKTOP_INSTALL,
        "result": OperatorReleaseResult.PASS,
        "operator_id": "operator-1",
        "package_version": "0.5.0.dev0",
        "skill_version": "sha256:" + "b" * 64,
        "source_commit_sha256": "c" * 64,
        "source_tree_sha256": "d" * 64,
        "evidence_sha256": "e" * 64,
    }
    values.update(changes)
    return RecordOperatorReleaseAttestation(**values)


def test_shadow_day_writer_stamps_utc_and_returns_canonical_digest() -> None:
    repository = _Observations()
    now = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)

    receipt = record_shadow_day_observation(
        _day_request(
            observed_run_ids=("premarket-2026-10-09-r1",),
            observed_feedback_ids=("feedback-1",),
        ),
        repository,
        _Calendar(),
        _Clock(now),
    )

    record = repository.get_observation(receipt.observation_id)
    assert record is not None
    assert isinstance(receipt, ShadowObservationReceipt)
    assert record.recorded_at == now
    assert receipt.recorded_at == now
    assert receipt.record_sha256 == sha256(canonical_bytes(record)).hexdigest()


def test_shadow_day_writer_persists_source_bound_safety_incident_observation() -> None:
    repository = _Observations()
    safety_observation = ShadowSafetyIncidentObservation(
        source_commit_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        reviewed_categories=tuple(ShadowSafetyIncidentCategory),
        incidents=(),
    )

    receipt = record_shadow_day_observation(
        _day_request(safety_incident_observation=safety_observation),
        repository,
        _Calendar(),
        _Clock(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)),
    )

    record = repository.get_observation(receipt.observation_id)
    assert record is not None
    assert record.schema_version == "0.2"
    assert record.safety_incident_observation == safety_observation
    assert receipt.record_sha256 == sha256(canonical_bytes(record)).hexdigest()


def test_legacy_shadow_day_request_remains_readable_without_safety_observation() -> None:
    repository = _Observations()
    request = _day_request(schema_version="0.1")

    receipt = record_shadow_day_observation(
        request,
        repository,
        _Calendar(),
        _Clock(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)),
    )

    record = repository.get_observation(receipt.observation_id)
    assert record is not None
    assert record.schema_version == "0.1"
    assert record.safety_incident_observation is None


def test_legacy_shadow_day_request_cannot_claim_safety_observation() -> None:
    safety_observation = ShadowSafetyIncidentObservation(
        source_commit_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        reviewed_categories=tuple(ShadowSafetyIncidentCategory),
        incidents=(),
    )

    with pytest.raises(
        ValidationError,
        match="schema 0.1 cannot include a safety incident observation",
    ):
        _day_request(schema_version="0.1", safety_incident_observation=safety_observation)


@pytest.mark.parametrize(
    ("market_date", "message"),
    (
        (date(2026, 10, 12), "cannot record a future shadow market date"),
        (date(2026, 10, 3), "must be a trading day"),
    ),
)
def test_shadow_day_writer_rejects_future_and_non_trading_dates(
    market_date: date, message: str
) -> None:
    repository = _Observations()
    with pytest.raises(ValueError, match=message):
        record_shadow_day_observation(
            _day_request(market_date=market_date),
            repository,
            _Calendar(),
            _Clock(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)),
        )
    assert repository.list_observations() == ()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("observed_run_ids", ("premarket-2026-10-09-r2",), "unknown run reference"),
        ("observed_feedback_ids", ("feedback-unknown",), "unknown feedback reference"),
    ),
)
def test_shadow_day_writer_rejects_unknown_run_and_feedback_references(
    field: str, value: tuple[str, ...], message: str
) -> None:
    repository = _Observations()
    with pytest.raises(ValueError, match=message):
        record_shadow_day_observation(
            _day_request(**{field: value}),
            repository,
            _Calendar(),
            _Clock(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)),
        )
    assert repository.list_observations() == ()


def test_shadow_day_correction_appends_a_hash_linked_record() -> None:
    repository = _Observations()
    clock = _Clock(datetime(2026, 10, 10, 0, 0, tzinfo=UTC))
    first = record_shadow_day_observation(_day_request(), repository, _Calendar(), clock)
    clock.current += timedelta(minutes=1)

    corrected = record_shadow_day_observation(
        _day_request(
            id="shadow-day-2",
            availability=ShadowDayAvailability.UNKNOWN,
            corrects_observation_id=first.observation_id,
            corrects_observation_sha256=first.record_sha256,
        ),
        repository,
        _Calendar(),
        clock,
    )

    assert len(repository.list_observations()) == 2
    correction = repository.get_observation(corrected.observation_id)
    assert correction is not None
    assert correction.corrects_observation_id == first.observation_id
    assert correction.corrects_observation_sha256 == first.record_sha256


def test_shadow_day_correction_must_match_a_stored_prior_record() -> None:
    repository = _Observations()
    with pytest.raises(ValueError, match="correction target does not exist"):
        record_shadow_day_observation(
            _day_request(
                id="shadow-day-2",
                corrects_observation_id="shadow-day-1",
                corrects_observation_sha256="a" * 64,
            ),
            repository,
            _Calendar(),
            _Clock(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)),
        )

    first = record_shadow_day_observation(
        _day_request(),
        repository,
        _Calendar(),
        _Clock(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)),
    )
    with pytest.raises(ValueError, match="correction target digest does not match"):
        record_shadow_day_observation(
            _day_request(
                id="shadow-day-3",
                corrects_observation_id=first.observation_id,
                corrects_observation_sha256="a" * 64,
            ),
            repository,
            _Calendar(),
            _Clock(datetime(2026, 10, 10, 0, 1, tzinfo=UTC)),
        )
    with pytest.raises(ValueError, match="must keep the same market date"):
        record_shadow_day_observation(
            _day_request(
                id="shadow-day-4",
                market_date=date(2026, 10, 8),
                corrects_observation_id=first.observation_id,
                corrects_observation_sha256=first.record_sha256,
            ),
            repository,
            _Calendar(),
            _Clock(datetime(2026, 10, 10, 0, 1, tzinfo=UTC)),
        )

    assert len(repository.list_observations()) == 1


def test_operator_attestation_is_service_stamped_and_append_only() -> None:
    repository = _Attestations()
    now = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)

    receipt = record_operator_release_attestation(
        _attestation_request(), repository, _Clock(now)
    )

    stored = repository.list_attestations()
    assert len(stored) == 1
    assert stored[0].recorded_at == now
    assert isinstance(receipt, OperatorReleaseAttestationReceipt)
    assert receipt.record_sha256 == sha256(canonical_bytes(stored[0])).hexdigest()


def test_shadow_services_reject_non_utc_service_clock_without_appending() -> None:
    observations = _Observations()
    attestations = _Attestations()
    bad_clock = _Clock(
        datetime(2026, 10, 10, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    )
    with pytest.raises(ValueError, match="clock must return a UTC timestamp"):
        record_shadow_day_observation(_day_request(), observations, _Calendar(), bad_clock)
    with pytest.raises(ValueError, match="clock must return a UTC timestamp"):
        record_operator_release_attestation(_attestation_request(), attestations, bad_clock)
    assert observations.list_observations() == ()
    assert attestations.list_attestations() == ()


def test_operator_requests_cannot_supply_service_recorded_timestamps() -> None:
    now = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
    observation = _day_request().model_dump(mode="python") | {"recorded_at": now}
    attestation = _attestation_request().model_dump(mode="python") | {"recorded_at": now}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RecordShadowDayObservation.model_validate(observation, strict=True)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RecordOperatorReleaseAttestation.model_validate(attestation, strict=True)
