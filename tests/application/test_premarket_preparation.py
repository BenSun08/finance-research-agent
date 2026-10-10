"""Public preparation must produce frozen synthesis input or a published failure."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.config_service import DirectoryConfigurationRepository
from finance_research_agent.application.run_service import (
    PreparePremarketRunRequest,
    RunDependencies,
)
from finance_research_agent.domain.enums import (
    Capability,
    Coverage,
    GateStatus,
    InvocationType,
    ReducedReportReason,
    Session,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
    ProviderReadiness,
)
from tests.support.component_versions import synthetic_component_versions

NOW = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "examples"


class Clock:
    def now_utc(self):
        return NOW


class Calendar:
    def is_trading_day(self, day):
        return day.weekday() < 5

    def session_open_close(self, day):
        return (datetime.combine(day, time(13, 30), UTC), datetime.combine(day, time(20), UTC))

    def readiness(self):
        return ProviderReadiness(provider="market-calendar", configured=True, available=True)


class MarketData:
    def __init__(self, *, available=True):
        self.available = available
        self.calls = []

    def readiness(self):
        self.calls.append("readiness")
        return ProviderReadiness(
            provider="alpaca",
            configured=True,
            available=self.available,
            error_code=None if self.available else ErrorCode.PROVIDER_UNAVAILABLE,
        )

    def fetch_instruments(self, symbols, **kwargs):
        self.calls.append("instruments")
        return {
            symbol: InstrumentIdentity(
                instrument_id=symbol,
                symbol=symbol,
                name=symbol,
                instrument_type="COMMON_STOCK",
                primary_exchange="NASDAQ",
                listing_country="US",
                currency="USD",
                is_active=True,
                is_leveraged=False,
                is_inverse=False,
                is_otc=False,
            )
            for symbol in symbols
        }

    def fetch_daily_bars(self, symbols, start, end, **kwargs):
        self.calls.append("bars")
        return {
            symbol: tuple(
                CompletedDailyBar(
                    instrument_id=symbol,
                    session_date=day,
                    source_timestamp=datetime.combine(day, time(20), UTC),
                    open=Decimal("100"),
                    high=Decimal("102"),
                    low=Decimal("99"),
                    close=Decimal("101"),
                    volume=1_000_000,
                    session=Session.COMPLETED_SESSION,
                    provider="alpaca",
                    feed="iex",
                    coverage=Coverage.SINGLE_EXCHANGE,
                    adjustment="SPLIT",
                    retrieved_at=NOW,
                    evidence_cutoff_at=NOW,
                    evidence_id=f"raw-{symbol}-{day}",
                    quality_flags=(),
                )
                for day in kwargs["expected_sessions"]
            )
            for symbol in symbols
        }

    def fetch_premarket_observations(self, symbols, as_of, **kwargs):
        self.calls.append("prices")
        return {
            symbol: PriceObservation(
                instrument_id=symbol,
                value=Decimal("101"),
                currency="USD",
                session=Session.PRE_MARKET,
                provider="alpaca",
                feed="iex",
                coverage=Coverage.SINGLE_EXCHANGE,
                observed_at=NOW - timedelta(minutes=1),
                retrieved_at=NOW,
                evidence_id=f"raw-price-{symbol}",
                quality_flags=(),
            )
            for symbol in symbols
        }


def dependencies(tmp_path, *, market=None):
    return RunDependencies(
        clock=Clock(),
        calendar=Calendar(),
        config_repository=DirectoryConfigurationRepository(CONFIG_ROOT),
        run_repository=FileSystemRunRepository(tmp_path),
        market_data=market or MarketData(),
        event_providers=(),
        component_versions=synthetic_component_versions,
    )


def request(revision=None, market_date=date(2026, 9, 28)):
    return PreparePremarketRunRequest(market_date, revision, InvocationType.MANUAL)


def test_prepare_reaches_frozen_packet_with_regime_and_explicit_event_exclusions(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    result = prepare_research_packet(request(), deps)
    assert result.outcome == "PACKET_READY"
    packet = result.research_packet
    assert packet.run.evidence_cutoff_at == NOW
    assert packet.run.execution_status.value == "AWAITING_SYNTHESIS"
    assert packet.regime_result is not None
    assert packet.regime_result.metrics
    assert packet.evidence and packet.market
    assert packet.candidates == packet.deterministic_plan_inputs == ()
    assert {item.symbol for item in packet.candidate_exclusions} == {"AAPL"}
    assert any("macro calendar health is missing" in gate.message for gate in packet.gates)
    assert result.stored_run.checkpoints[-1].stage == "AWAITING_SYNTHESIS"
    assert deps.run_repository.read_staged_artifact(packet.run.run_id, "reduced_report")


def test_resume_returns_exact_frozen_packet_without_provider_or_current_configuration(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    first = prepare_research_packet(request(), deps)

    class Unavailable:
        def __getattr__(self, name):
            raise AssertionError("resume must not access current dependencies")

    resumed = prepare_research_packet(
        request(1),
        replace(
            deps,
            market_data=Unavailable(),
            config_repository=Unavailable(),
            component_versions=Unavailable(),
        ),
    )
    assert resumed.research_packet == first.research_packet
    assert resumed.stored_run == first.stored_run


def test_global_quality_failure_publishes_operational_report_without_packet(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path, market=MarketData(available=False))
    result = prepare_research_packet(request(), deps)
    assert result.outcome == "PUBLISHED"
    assert result.research_packet is None
    assert result.failure_code is ErrorCode.PROVIDER_UNAVAILABLE
    assert result.stored_run.published
    report = deps.run_repository.get_report(result.stored_run.run_id)
    assert "Brief origin: OPERATIONAL" in report
    assert "PROVIDER_UNAVAILABLE" in report


def test_preparation_publishes_missed_window_before_collecting_market_data(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    market = MarketData()
    deps = dependencies(tmp_path, market=market)

    class MissedClock:
        def now_utc(self):
            return datetime(2026, 9, 28, 13, 30, tzinfo=UTC)

    result = prepare_research_packet(request(), replace(deps, clock=MissedClock()))

    assert result.outcome == "PUBLISHED"
    assert result.failure_code.value == "MISSED_WINDOW"
    assert result.stored_run.run.delivery_status.value == "MISSED_WINDOW"
    assert market.calls == []
    report = deps.run_repository.get_report(result.stored_run.run_id)
    assert "Brief origin: OPERATIONAL" in report
    assert "MISSED_WINDOW" in report


def test_after_close_missed_run_is_durable_without_formal_publication_or_provider_reads(
    tmp_path,
):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    market = MarketData()
    deps = dependencies(tmp_path, market=market)

    class AfterCloseClock:
        def now_utc(self):
            return datetime(2026, 9, 28, 20, 0, tzinfo=UTC)

    missed_run_reader = getattr(deps.run_repository, "get_missed_run", None)
    assert callable(missed_run_reader), "missed-run records require a durable repository read"

    result = prepare_research_packet(request(), replace(deps, clock=AfterCloseClock()))

    assert result.outcome == "SKIPPED"
    assert result.window_decision.missed_record_only is True
    record = missed_run_reader(date(2026, 9, 28))
    assert record is not None
    assert record.market_date == date(2026, 9, 28)
    assert record.reason_code == "MISSED_WINDOW"
    assert record.detected_at == datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    assert record.regular_close_at == datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    assert market.calls == []
    assert deps.run_repository.get_latest(date(2026, 9, 28)) is None
    assert deps.run_repository._existing_revision_ids(date(2026, 9, 28)) == ()

    retry = prepare_research_packet(request(), replace(deps, clock=AfterCloseClock()))

    assert retry.outcome == "SKIPPED"
    assert missed_run_reader(date(2026, 9, 28)) == record


def test_late_resume_publishes_missed_window_without_reusing_staged_packet(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    market = MarketData()
    deps = dependencies(tmp_path, market=market)
    first = prepare_research_packet(request(1), deps)
    assert first.outcome == "PACKET_READY"
    calls_before_retry = tuple(market.calls)

    class MissedClock:
        def now_utc(self):
            return datetime(2026, 9, 28, 13, 30, tzinfo=UTC)

    retry = prepare_research_packet(request(1), replace(deps, clock=MissedClock()))

    assert retry.outcome == "PUBLISHED"
    assert retry.failure_code is ErrorCode.MISSED_WINDOW
    assert retry.stored_run.run_id == first.stored_run.run_id
    assert retry.stored_run.run.delivery_status is first.stored_run.run.delivery_status
    assert retry.stored_run.published is True
    assert tuple(market.calls) == calls_before_retry
    published_bundle = deps.run_repository.load_published_bundle(retry.stored_run.run_id)
    assert published_bundle is not None
    assert published_bundle.bundle["brief_origin"] == "OPERATIONAL"
    assert published_bundle.bundle["failure_code"] == "MISSED_WINDOW"
    assert "research_packet" not in published_bundle.bundle
    assert any(
        checkpoint.artifact_hashes.get("research_packet")
        for checkpoint in retry.stored_run.checkpoints
    )
    report = deps.run_repository.get_report(retry.stored_run.run_id)
    assert "MISSED_WINDOW" in report


def test_after_close_resume_records_miss_and_skips_staged_packet(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    market = MarketData()
    deps = dependencies(tmp_path, market=market)
    first = prepare_research_packet(request(1), deps)
    assert first.outcome == "PACKET_READY"
    calls_before_retry = tuple(market.calls)

    class AfterCloseClock:
        def now_utc(self):
            return datetime(2026, 9, 28, 20, 0, tzinfo=UTC)

    late_deps = replace(deps, clock=AfterCloseClock())
    retry = prepare_research_packet(request(1), late_deps)

    assert retry.outcome == "SKIPPED"
    assert retry.window_decision.missed_record_only is True
    assert retry.stored_run is None
    assert deps.run_repository.load(first.stored_run.run_id) == first.stored_run
    record = deps.run_repository.get_missed_run(date(2026, 9, 28))
    assert record is not None
    assert record.detected_at == datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    assert deps.run_repository.get_latest(date(2026, 9, 28)) is None
    assert tuple(market.calls) == calls_before_retry

    repeated = prepare_research_packet(request(1), late_deps)

    assert repeated.outcome == "SKIPPED"
    assert deps.run_repository.get_missed_run(date(2026, 9, 28)) == record


def test_unexpected_runtime_during_resume_window_check_propagates(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    from finance_research_agent.application import premarket_preparation

    deps = dependencies(tmp_path)
    first = premarket_preparation.prepare_research_packet(request(1), deps)
    assert first.outcome == "PACKET_READY"

    def fail_unexpectedly(*_args, **_kwargs):
        raise RuntimeError("unexpected-window-check-error")

    monkeypatch.setattr(premarket_preparation, "resolve_run_window", fail_unexpectedly)
    with pytest.raises(RuntimeError, match="unexpected-window-check-error"):
        premarket_preparation.prepare_research_packet(request(1), deps)


def test_unknown_regime_disables_usable_classification_and_discloses_missing_evidence(
    tmp_path,
):
    from finance_research_agent.application.premarket_preparation import (
        _unknown_regime_projection,
        prepare_research_packet,
    )
    from finance_research_agent.application.reduced_report import render_reduced_report
    from finance_research_agent.domain.regime import RegimePolicy, calculate_regime

    class MissingBroadData(MarketData):
        def fetch_daily_bars(self, symbols, start, end, **kwargs):
            outcomes = dict(super().fetch_daily_bars(symbols, start, end, **kwargs))
            outcomes["SPY"] = ProviderFailure(
                provider="alpaca",
                symbol="SPY",
                error_code=ErrorCode.PROVIDER_NO_DATA,
                retryable=False,
            )
            return outcomes

    baseline_result = prepare_research_packet(
        request(), dependencies(tmp_path / "baseline", market=MarketData())
    )
    baseline_packet = baseline_result.research_packet
    assert baseline_packet is not None
    baseline_capabilities = {
        state.capability: state for state in baseline_packet.capability_states
    }

    packet_result = prepare_research_packet(
        request(), dependencies(tmp_path, market=MissingBroadData())
    )

    assert packet_result.outcome == "PACKET_READY"
    packet = packet_result.research_packet
    assert packet is not None and packet.regime_result is not None
    assert packet.regime_result.regime.value == "unknown"
    missing_evidence = tuple(
        item.evidence_id
        for item in packet.evidence
        if item.structured_fields.get("outcome") == "DAILY_BARS"
        and item.structured_fields.get("requested_symbol") == "SPY"
        and item.structured_fields.get("error_code") == ErrorCode.PROVIDER_NO_DATA.value
    )
    assert len(missing_evidence) == 1
    capabilities = {state.capability: state for state in packet.capability_states}
    regime = capabilities[Capability.REGIME_CLASSIFICATION_AVAILABLE]
    assert regime.available is False
    assert regime.reason_codes == (ErrorCode.PROVIDER_NO_DATA,)
    assert regime.evidence_ids == missing_evidence
    plan = capabilities[Capability.PLAN_DRAFT_AVAILABLE]
    assert plan.available is False
    assert missing_evidence[0] in plan.evidence_ids
    assert plan.reason_codes == tuple(dict.fromkeys((
        *baseline_capabilities[Capability.PLAN_DRAFT_AVAILABLE].reason_codes,
        ErrorCode.PROVIDER_NO_DATA,
    )))
    unknown_gates = tuple(
        gate for gate in packet.gates if gate.gate_id == "regime-classification-unknown"
    )
    assert len(unknown_gates) == 1
    assert unknown_gates[0].status is GateStatus.BLOCK
    assert unknown_gates[0].reason_code == ErrorCode.PROVIDER_NO_DATA.value
    assert unknown_gates[0].evidence_ids == missing_evidence
    assert unknown_gates[0].capability is Capability.REGIME_CLASSIFICATION_AVAILABLE
    assert unknown_gates[0].rule_version == packet.regime_result.formula_version
    for capability in Capability:
        if capability not in {
            Capability.REGIME_CLASSIFICATION_AVAILABLE,
            Capability.PLAN_DRAFT_AVAILABLE,
        }:
            assert capabilities[capability] == baseline_capabilities[capability]
    assert packet.deterministic_plan_inputs == ()

    evidence_free_unknown = calculate_regime({}, RegimePolicy(), NOW)
    unchanged_capabilities, no_disclosure = _unknown_regime_projection(
        evidence_free_unknown,
        baseline_packet.evidence,
        baseline_packet.capability_states,
        ("SPY", "QQQ"),
    )
    assert unchanged_capabilities == baseline_packet.capability_states
    assert no_disclosure is None

    report = render_reduced_report(packet, ReducedReportReason.SYNTHESIS_UNAVAILABLE)
    assert "Regime classification is UNKNOWN" in report
    assert missing_evidence[0] in report


def test_explicit_published_revision_returns_original_receipt_without_new_collection(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    market = MarketData(available=False)
    deps = dependencies(tmp_path, market=market)
    first = prepare_research_packet(request(), deps)
    calls = tuple(market.calls)
    second = prepare_research_packet(request(1), deps)
    assert second.publication == first.publication
    assert tuple(market.calls) == calls


def test_non_trading_day_does_not_allocate_or_collect(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    market = MarketData()
    deps = dependencies(tmp_path, market=market)
    result = prepare_research_packet(request(market_date=date(2026, 9, 27)), deps)
    assert result.outcome == "SKIPPED"
    assert result.stored_run is result.research_packet is result.publication is None
    assert market.calls == []


def test_packet_budget_failure_publishes_closed_operational_result(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    result = prepare_research_packet(request(), deps, max_packet_bytes=1)
    assert result.outcome == "PUBLISHED"
    assert result.research_packet is None
    assert result.failure_code is ErrorCode.INTERNAL_ERROR
    assert (
        deps.run_repository.read_staged_artifact(result.stored_run.run_id, "research_packet")
        is None
    )


def test_public_prepare_operation_returns_typed_packet_handoff(tmp_path):
    from finance_research_agent.application.operations import OPERATION_CONTRACTS
    from finance_research_agent.application.premarket_preparation import PreparedPremarketRunResult
    from finance_research_agent.application.services import ApplicationServices

    deps = dependencies(tmp_path)
    services = ApplicationServices(
        clock=deps.clock,
        calendar=deps.calendar,
        configuration_repository=deps.config_repository,
        market_data=deps.market_data,
        run_repository=deps.run_repository,
        published_artifact_reader=deps.run_repository,
        watchlist_repository=object(),
        feedback_repository=object(),
        component_versions=deps.component_versions,
    )
    result = services.dispatch("prepare_premarket_run", '{"market_date":"2026-09-28"}')
    assert isinstance(result, PreparedPremarketRunResult)
    assert result.outcome == "PACKET_READY"
    assert OPERATION_CONTRACTS["prepare_premarket_run"].result_model is PreparedPremarketRunResult


def test_prior_selection_is_frozen_before_first_provider_read(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    original = deps.market_data.fetch_instruments

    def inspect_selection(*args, **kwargs):
        stored = deps.run_repository.load("premarket-2026-09-28-r1")
        assert any("prior_research" in item.artifact_hashes for item in stored.checkpoints)
        return original(*args, **kwargs)

    deps.market_data.fetch_instruments = inspect_selection
    assert prepare_research_packet(request(), deps).outcome == "PACKET_READY"


def test_expired_allocated_run_publishes_deadline_without_provider_reads(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet
    from finance_research_agent.application.run_service import prepare_premarket_run

    deps = dependencies(tmp_path)
    prepare_premarket_run(request(), deps)

    class ExpiredClock:
        def now_utc(self):
            return NOW + timedelta(minutes=15)

    result = prepare_research_packet(request(1), replace(deps, clock=ExpiredClock()))
    assert result.outcome == "PUBLISHED"
    assert result.failure_code is ErrorCode.DEADLINE_EXCEEDED
    assert deps.market_data.calls == []


def test_run_factory_receives_frozen_identity_and_trusted_deadline(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    calls = []

    def factory(run, deadline):
        calls.append((run, deadline))
        return deps.market_data

    result = prepare_research_packet(request(), deps, market_data_for_run=factory)
    assert calls == [
        (
            result.stored_run.run.model_copy(update={"evidence_cutoff_at": None}),
            NOW + timedelta(minutes=15),
        )
    ]


def test_factory_timeout_publishes_closed_failure(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    def factory(run, deadline):
        raise TimeoutError("private-transport-value")

    result = prepare_research_packet(request(), dependencies(tmp_path), market_data_for_run=factory)
    assert result.failure_code is ErrorCode.DEADLINE_EXCEEDED
    assert "private-transport-value" not in repr(result)


def test_postfreeze_deadline_publishes_without_packet(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    class AdvancingClock:
        late = False

        def now_utc(self):
            return NOW + timedelta(minutes=15) if self.late else NOW

    clock = AdvancingClock()
    deps = dependencies(tmp_path)
    original = deps.market_data.readiness

    def readiness():
        clock.late = True
        return original()

    deps.market_data.readiness = readiness
    result = prepare_research_packet(request(), replace(deps, clock=clock))
    assert result.failure_code is ErrorCode.DEADLINE_EXCEEDED
    assert result.research_packet is None


@pytest.mark.parametrize("limit", [0, -1, True, "100"])
def test_invalid_trusted_packet_limit_is_rejected_before_allocating(tmp_path, limit):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    with pytest.raises(ValueError, match="positive integer"):
        prepare_research_packet(request(), deps, max_packet_bytes=limit)
    assert deps.run_repository.load("premarket-2026-09-28-r1") is None


def test_complete_prepared_packet_can_be_published_and_reloaded(tmp_path):
    import json

    from finance_research_agent.application.operations import ReducedReportReason
    from finance_research_agent.application.premarket_preparation import prepare_research_packet
    from finance_research_agent.application.publication_service import publish_reduced_report
    from finance_research_agent.domain.packets import ResearchPacket

    deps = dependencies(tmp_path)
    prepared = prepare_research_packet(request(), deps)
    published = publish_reduced_report(
        deps.run_repository,
        prepared.research_packet,
        ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        NOW,
    )
    bundle = deps.run_repository.load_published_bundle(published.run_id)
    restored = ResearchPacket.model_validate_json(
        json.dumps(bundle.model_dump(mode="json")["bundle"]["research_packet"]), strict=True
    )
    assert restored == prepared.research_packet
    assert bundle.run.execution_status.value == "PUBLISHED"


def test_staged_operational_reason_recovers_after_checkpoint_crash_even_after_deadline(
    tmp_path,
    monkeypatch,
):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    original = deps.run_repository.checkpoint_if_current

    def interrupted(run_id, checkpoint, expected_count):
        if checkpoint.stage == "PREPARATION_FAILED":
            raise OSError("checkpoint interruption")
        return original(run_id, checkpoint, expected_count)

    monkeypatch.setattr(deps.run_repository, "checkpoint_if_current", interrupted)
    with pytest.raises(OSError):
        prepare_research_packet(request(), deps, max_packet_bytes=1)
    monkeypatch.setattr(deps.run_repository, "checkpoint_if_current", original)

    class LateClock:
        def now_utc(self):
            return NOW + timedelta(minutes=20)

    result = prepare_research_packet(request(1), replace(deps, clock=LateClock()))
    assert result.outcome == "PUBLISHED"
    assert result.failure_code is ErrorCode.INTERNAL_ERROR


def test_staged_operational_quality_recovery_rejects_changed_quality_artifact(
    tmp_path,
    monkeypatch,
):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path)
    original = deps.run_repository.checkpoint_if_current

    def interrupted(run_id, checkpoint, expected_count):
        if checkpoint.stage == "PREPARATION_FAILED":
            raise OSError("checkpoint interruption")
        return original(run_id, checkpoint, expected_count)

    monkeypatch.setattr(deps.run_repository, "checkpoint_if_current", interrupted)
    with pytest.raises(OSError):
        prepare_research_packet(request(), deps, max_packet_bytes=1)
    monkeypatch.setattr(deps.run_repository, "checkpoint_if_current", original)

    from finance_research_agent.domain.market_calendar import format_run_id

    run_id = format_run_id(date(2026, 9, 28), 1)
    assert deps.run_repository.load(run_id) is not None
    quality_path = (
        tmp_path
        / "runs"
        / "2026"
        / "2026-09-28"
        / ".staging"
        / run_id
        / "artifacts"
        / "data_quality.bin"
    )
    quality_path.write_bytes(b"tampered quality bytes")

    with pytest.raises(ValueError, match="staged data quality differs from checkpoint hash"):
        prepare_research_packet(request(1), deps)


def test_known_historical_calendar_failure_publishes_closed_operational_report(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    class MissingHistory(Calendar):
        def is_trading_day(self, day):
            if day != date(2026, 9, 28):
                raise RuntimeError("private-calendar-error")
            return True

    deps = replace(dependencies(tmp_path), calendar=MissingHistory())
    result = prepare_research_packet(request(), deps)
    assert result.outcome == "PUBLISHED"
    assert result.failure_code is ErrorCode.MARKET_CALENDAR_UNAVAILABLE
    assert "private-calendar-error" not in repr(result)


def test_calendar_failure_during_allocated_deadline_lookup_publishes_closed_failure(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    class ChangedCalendar(Calendar):
        calls = 0

        def session_open_close(self, day):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("private-calendar-deadline-error")
            return super().session_open_close(day)

    deps = replace(dependencies(tmp_path), calendar=ChangedCalendar())
    result = prepare_research_packet(request(), deps)
    assert result.outcome == "PUBLISHED"
    assert result.failure_code is ErrorCode.MARKET_CALENDAR_UNAVAILABLE
    assert "private-calendar-deadline-error" not in repr(result)


def test_published_shortcut_preserves_scheduled_revision_rejection(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path, market=MarketData(available=False))
    prepare_research_packet(request(), deps)
    scheduled = PreparePremarketRunRequest(date(2026, 9, 28), 1, InvocationType.SCHEDULED)
    with pytest.raises(ValueError, match="scheduled"):
        prepare_research_packet(scheduled, deps)


def test_typed_provider_deadline_publishes_failure_even_without_wall_clock_advance(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet
    from finance_research_agent.domain.models import ProviderFailure

    deps = dependencies(tmp_path)
    original = deps.market_data.fetch_daily_bars

    def deadline(*args, **kwargs):
        values = original(*args, **kwargs)
        values["AAPL"] = ProviderFailure(
            provider="alpaca",
            symbol="AAPL",
            error_code=ErrorCode.DEADLINE_EXCEEDED,
            retryable=False,
        )
        return values

    deps.market_data.fetch_daily_bars = deadline
    result = prepare_research_packet(request(), deps)
    assert result.outcome == "PUBLISHED"
    assert result.failure_code is ErrorCode.DEADLINE_EXCEEDED
    assert result.research_packet is None


def test_resume_after_quality_checkpoint_does_not_reconstruct_current_provider(
    tmp_path,
    monkeypatch,
):
    import finance_research_agent.application.premarket_preparation as preparation

    deps = dependencies(tmp_path)
    original = preparation.build_research_packet

    def interrupted(*args, **kwargs):
        raise OSError("analysis interruption")

    monkeypatch.setattr(preparation, "build_research_packet", interrupted)
    with pytest.raises(OSError):
        preparation.prepare_research_packet(request(), deps)
    monkeypatch.setattr(preparation, "build_research_packet", original)

    def forbidden_factory(*args):
        raise AssertionError("provider reconstruction after frozen quality")

    result = preparation.prepare_research_packet(
        request(1),
        deps,
        market_data_for_run=forbidden_factory,
    )
    assert result.outcome == "PACKET_READY"


def test_published_shortcut_rejects_metadata_that_differs_from_immutable_bundle(tmp_path):
    import json

    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path, market=MarketData(available=False))
    published = prepare_research_packet(request(), deps)
    path = tmp_path / "runs/2026/2026-09-28" / published.stored_run.run_id / "run.json"
    value = json.loads(path.read_bytes())
    value["prompt_version"] = "tampered-prompt"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="published.*context"):
        prepare_research_packet(request(1), deps)


def test_manual_published_resume_preserves_original_scheduled_window(tmp_path):
    from finance_research_agent.application.premarket_preparation import prepare_research_packet

    deps = dependencies(tmp_path, market=MarketData(available=False))
    scheduled = PreparePremarketRunRequest(date(2026, 9, 28), None, InvocationType.SCHEDULED)
    original = prepare_research_packet(scheduled, deps)
    resumed = prepare_research_packet(request(1), deps)
    assert resumed.publication == original.publication
    assert resumed.stored_run == original.stored_run
    assert resumed.window_decision == original.window_decision
