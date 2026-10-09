"""R11 failure injections against real application and repository services."""

import json
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from finance_research_agent.domain.enums import (
    BriefOrigin,
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    GateStatus,
    PlanStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import ProviderFailure
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from tests.application.test_premarket_preparation import (
    MarketData,
    dependencies,
    request,
)


def test_f01_global_alpaca_outage_publishes_only_a_hashed_operational_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from finance_research_agent.application import premarket_preparation
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )

    class GlobalOutage(MarketData):
        def __init__(self):
            super().__init__(available=False)
            self.calls = []

        def fetch_instruments(self, symbols, **kwargs):
            self.calls.append("instruments")
            return {
                symbol: ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                )
                for symbol in symbols
            }

        def fetch_daily_bars(self, symbols, start, end, **kwargs):
            self.calls.append("daily_bars")
            return {
                symbol: ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                )
                for symbol in symbols
            }

        def fetch_premarket_observations(self, symbols, as_of, **kwargs):
            self.calls.append("premarket")
            return {
                symbol: ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                )
                for symbol in symbols
            }

    provider = GlobalOutage()
    deps = dependencies(tmp_path, market=provider)
    market_date = date(2026, 9, 28)
    assert deps.run_repository.get_latest(market_date) is None

    captured_quality = []
    checkpoint_quality = premarket_preparation.checkpoint_collected_market_data_quality

    def capture_quality(*args, **kwargs):
        value = checkpoint_quality(*args, **kwargs)
        captured_quality.append(value.quality)
        return value

    monkeypatch.setattr(
        premarket_preparation,
        "checkpoint_collected_market_data_quality",
        capture_quality,
    )

    result = premarket_preparation.prepare_research_packet(request(), deps)

    assert result.outcome == "PUBLISHED"
    assert result.failure_code is ErrorCode.PROVIDER_UNAVAILABLE
    assert result.research_packet is None
    assert result.stored_run is not None
    assert len(captured_quality) == 1
    quality = captured_quality[0]
    assert quality.status is DataQualityStatus.FAIL
    assert tuple(state.capability for state in quality.capabilities) == tuple(Capability)
    assert all(not state.available for state in quality.capabilities)
    assert all(
        ErrorCode.PROVIDER_UNAVAILABLE in state.reason_codes
        for state in quality.capabilities
    )

    assert provider.calls == ["instruments", "daily_bars", "premarket", "readiness"]

    stored = result.stored_run
    assert all(
        "research_packet" not in checkpoint.artifact_hashes
        for checkpoint in stored.checkpoints
    )
    assert deps.run_repository.read_staged_artifact(stored.run_id, "research_packet") is None
    assert deps.run_repository.get_latest(market_date) == stored.run_id
    bundle = deps.run_repository.load_published_bundle(stored.run_id)
    report = deps.run_repository.get_report(stored.run_id)
    artifact = deps.run_repository.get_published_artifact(stored.run_id)
    assert bundle is not None and report is not None and artifact is not None
    assert bundle.run.execution_status is ExecutionStatus.PUBLISHED
    assert bundle.run.data_quality_status is DataQualityStatus.FAIL
    assert bundle.run.delivery_status is DeliveryStatus.MANUAL
    assert bundle.bundle["brief_origin"] == BriefOrigin.OPERATIONAL.value
    assert bundle.bundle["failure_code"] == ErrorCode.PROVIDER_UNAVAILABLE.value
    assert "Brief origin: OPERATIONAL" in report
    assert "Data quality status: FAIL" in report
    assert "Failure code: PROVIDER_UNAVAILABLE" in report
    assert "No market conclusion or trade plan is available." in report
    assert "SPY" not in report and "QQQ" not in report
    assert result.publication == artifact
    assert artifact.bundle_sha256 == sha256(canonical_bytes(bundle)).hexdigest()
    assert artifact.markdown_sha256 == sha256(report.encode("utf-8")).hexdigest()
    replay = replay_published_artifact(
        deps.run_repository, stored.run_id, _recorded_versions(bundle)
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f02_one_symbol_history_failure_is_scoped_and_other_data_survives(
    tmp_path: Path, monkeypatch
) -> None:
    from finance_research_agent.application import premarket_preparation
    from finance_research_agent.application.premarket_preparation import prepare_research_packet
    from finance_research_agent.application.publication_service import publish_reduced_report
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )

    class OneSymbolHistoryFailure(MarketData):
        def fetch_daily_bars(self, symbols, start, end, **kwargs):
            outcomes = dict(super().fetch_daily_bars(symbols, start, end, **kwargs))
            outcomes["AAPL"] = ProviderFailure(
                provider="alpaca",
                symbol="AAPL",
                error_code=ErrorCode.PROVIDER_NO_DATA,
                retryable=False,
            )
            return outcomes

    provider = OneSymbolHistoryFailure()
    deps = dependencies(tmp_path, market=provider)
    captured_quality = []
    checkpoint_quality = premarket_preparation.checkpoint_collected_market_data_quality

    def capture_quality(*args, **kwargs):
        value = checkpoint_quality(*args, **kwargs)
        captured_quality.append(value.quality)
        return value

    monkeypatch.setattr(
        premarket_preparation,
        "checkpoint_collected_market_data_quality",
        capture_quality,
    )
    prepared = prepare_research_packet(request(), deps)

    assert prepared.outcome == "PACKET_READY"
    assert prepared.failure_code is None
    packet = prepared.research_packet
    assert packet is not None
    assert packet.run.data_quality_status is DataQualityStatus.DEGRADED
    assert packet.run.execution_status is ExecutionStatus.AWAITING_SYNTHESIS
    assert packet.run.delivery_status is DeliveryStatus.MANUAL
    assert len(captured_quality) == 1
    quality = captured_quality[0]
    assert quality.symbol_plan_status("AAPL") is PlanStatus.BLOCKED
    aapl_plan = quality.symbol_capability("AAPL", Capability.PLAN_DRAFT_AVAILABLE)
    assert aapl_plan.available is False
    assert ErrorCode.PROVIDER_NO_DATA in aapl_plan.reason_codes
    assert quality.capability(Capability.REGIME_CLASSIFICATION_AVAILABLE).available is True
    assert provider.calls == ["instruments", "bars", "prices", "readiness"]
    assert packet.regime_result is not None
    assert packet.regime_result.regime.value != "unknown"
    assert packet.deterministic_plan_inputs == ()
    missing_evidence = tuple(
        item.evidence_id
        for item in packet.evidence
        if item.structured_fields.get("outcome") == "DAILY_BARS"
        and item.structured_fields.get("requested_symbol") == "AAPL"
        and item.structured_fields.get("error_code") == ErrorCode.PROVIDER_NO_DATA.value
    )
    assert len(missing_evidence) == 1
    aapl_exclusions = tuple(
        item for item in packet.candidate_exclusions if item.symbol == "AAPL"
    )
    assert len(aapl_exclusions) == 1
    assert "REQUIRED_DATA_MISSING" in aapl_exclusions[0].reason_codes
    scoped_gates = aapl_exclusions[0].gates
    assert scoped_gates
    assert all(gate.gate_id.startswith("AAPL-") for gate in scoped_gates)
    assert any(gate.reason_code == "PROVIDER_MISSING_SESSION" for gate in scoped_gates)
    assert any(ErrorCode.PROVIDER_NO_DATA.value in gate.message for gate in scoped_gates)
    assert "SPY" in packet.market
    assert "QQQ" in packet.market

    receipt = publish_reduced_report(
        deps.run_repository,
        packet,
        ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        deps.clock.now_utc(),
    )
    report = deps.run_repository.get_report(receipt.run_id)
    bundle = deps.run_repository.load_published_bundle(receipt.run_id)
    assert report is not None and bundle is not None
    assert bundle.bundle["brief_origin"] == BriefOrigin.DETERMINISTIC_REDUCED.value
    assert bundle.run.execution_status is ExecutionStatus.PUBLISHED
    assert bundle.run.data_quality_status is DataQualityStatus.DEGRADED
    assert bundle.run.delivery_status is DeliveryStatus.MANUAL
    assert deps.run_repository.get_latest(packet.run.market_date) == receipt.run_id
    assert receipt.bundle_sha256 == sha256(canonical_bytes(bundle)).hexdigest()
    assert receipt.markdown_sha256 == sha256(report.encode("utf-8")).hexdigest()
    replay = replay_published_artifact(
        deps.run_repository, receipt.run_id, _recorded_versions(bundle)
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True
    assert "Brief origin: DETERMINISTIC_REDUCED" in report
    assert "AAPL" in report
    assert missing_evidence[0] in report


def test_f03_stale_current_price_preserves_daily_regime_but_blocks_sizing(
    tmp_path: Path, monkeypatch
) -> None:
    from finance_research_agent.application import premarket_preparation
    from finance_research_agent.application.premarket_preparation import prepare_research_packet
    from finance_research_agent.application.publication_service import publish_reduced_report
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )

    class StaleCurrentPrice(MarketData):
        def fetch_premarket_observations(self, symbols, as_of, **kwargs):
            outcomes = dict(super().fetch_premarket_observations(symbols, as_of, **kwargs))
            outcomes["AAPL"] = ProviderFailure(
                provider="alpaca",
                symbol="AAPL",
                error_code=ErrorCode.STALE_DATA,
                retryable=False,
            )
            return outcomes

    provider = StaleCurrentPrice()
    deps = dependencies(tmp_path, market=provider)
    captured_quality = []
    checkpoint_quality = premarket_preparation.checkpoint_collected_market_data_quality

    def capture_quality(*args, **kwargs):
        value = checkpoint_quality(*args, **kwargs)
        captured_quality.append(value.quality)
        return value

    monkeypatch.setattr(
        premarket_preparation,
        "checkpoint_collected_market_data_quality",
        capture_quality,
    )
    prepared = prepare_research_packet(request(), deps)

    assert prepared.outcome == "PACKET_READY"
    packet = prepared.research_packet
    assert packet is not None and packet.regime_result is not None
    assert packet.run.data_quality_status is DataQualityStatus.DEGRADED
    assert packet.regime_result.regime.value != "unknown"
    assert "AAPL" in packet.market
    assert packet.market["AAPL"].completed_daily_bars
    assert packet.market["AAPL"].latest_price is None
    assert packet.deterministic_plan_inputs == ()
    assert len(captured_quality) == 1
    sizing = captured_quality[0].symbol_capability(
        "AAPL", Capability.POSITION_SIZING_AVAILABLE
    )
    assert sizing.available is False
    assert ErrorCode.STALE_DATA in sizing.reason_codes
    failure_evidence = tuple(
        item.evidence_id
        for item in packet.evidence
        if item.structured_fields.get("requested_symbol") == "AAPL"
        and item.structured_fields.get("error_code") == ErrorCode.STALE_DATA.value
    )
    assert len(failure_evidence) == 1
    stale_gates = tuple(
        gate for gate in packet.gates if gate.reason_code == ErrorCode.STALE_DATA.value
    )
    assert len(stale_gates) == 1
    assert stale_gates[0].status is GateStatus.BLOCK
    assert stale_gates[0].capability is Capability.POSITION_SIZING_AVAILABLE
    assert stale_gates[0].evidence_ids == failure_evidence
    assert "AAPL" in stale_gates[0].message and "stale" in stale_gates[0].message.lower()
    assert provider.calls == ["instruments", "bars", "prices", "readiness"]

    receipt = publish_reduced_report(
        deps.run_repository,
        packet,
        ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        deps.clock.now_utc(),
    )
    report = deps.run_repository.get_report(receipt.run_id)
    bundle = deps.run_repository.load_published_bundle(receipt.run_id)
    assert report is not None and bundle is not None
    assert "Brief origin: DETERMINISTIC_REDUCED" in report
    assert "AAPL" in report
    assert ErrorCode.STALE_DATA.value.replace("_", "\\_") in report
    assert failure_evidence[0] in report
    assert receipt.bundle_sha256 == sha256(canonical_bytes(bundle)).hexdigest()
    assert receipt.markdown_sha256 == sha256(report.encode("utf-8")).hexdigest()
    replay = replay_published_artifact(
        deps.run_repository, receipt.run_id, _recorded_versions(bundle)
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f04_conflicting_event_fixture_stays_out_of_current_source_publication(
    tmp_path: Path,
) -> None:
    from finance_research_agent.domain.enums import ReducedReportReason
    from finance_research_agent.evaluation import (
        EvaluationHarness,
        execute_evaluation_scenario,
    )
    from finance_research_agent.evaluation.models import FixtureSetId
    from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
    from tests.evaluation.fixture_bank import build_domain_fixture_bank

    scenario = load_evaluation_scenarios()[8]
    assert scenario.id.value == "S09"
    created_dependencies = []

    def create_dependencies(current_scenario, expectation):
        value = dependencies(tmp_path)
        created_dependencies.append(value)
        return value

    harness = EvaluationHarness(
        dependencies_factory=create_dependencies,
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        domain_fixtures=build_domain_fixture_bank(),
    )

    outcome = execute_evaluation_scenario(scenario, harness)

    assert outcome.assertions_failed == ()
    assert outcome.assertions_pending == ("SOURCE_LIMITATIONS_ADJACENT",)
    assert len(outcome.domain_assertion_outcomes) == 1
    domain_result = outcome.domain_assertion_outcomes[0]
    assert domain_result.fixture_id is FixtureSetId.S09_SOURCE_CONFLICT
    assert domain_result.status == "PASS"
    assert "gate_reason_codes" in domain_result.matched_fields
    assert len(outcome.current_scope_outcomes) == 1
    current = outcome.current_scope_outcomes[0]
    assert current.current_scope == scenario.current_scope_expectation.primary
    assert current.replay_json_matches is True
    assert current.replay_markdown_matches is True
    assert len(created_dependencies) == 1
    deps = created_dependencies[0]
    run_id = deps.run_repository.get_latest(harness.market_date)
    assert run_id is not None
    bundle = deps.run_repository.load_published_bundle(run_id)
    report = deps.run_repository.get_report(run_id)
    artifact = deps.run_repository.get_published_artifact(run_id)
    assert bundle is not None and report is not None and artifact is not None
    packet = bundle.bundle["research_packet"]
    assert isinstance(packet, FrozenMap)
    assert packet["events"] == ()
    event_capability = next(
        item
        for item in packet["capability_states"]
        if item["capability"] == Capability.EVENT_RISK_CHECK_AVAILABLE.value
    )
    assert event_capability["available"] is False
    assert event_capability["reason_codes"] == (ErrorCode.SOURCE_NOT_CONFIGURED.value,)
    assert packet["deterministic_plan_inputs"] == ()
    assert "Brief origin: DETERMINISTIC_REDUCED" in report
    assert "SOURCE_NOT_CONFIGURED" in report
    assert "SOURCE_CONFLICT" not in report
    assert "earnings" not in report.lower()
    assert deps.market_data.calls == ["instruments", "bars", "prices", "readiness"]
    assert deps.run_repository.get_latest(harness.market_date) == run_id
    assert current.artifact_hashes["research_packet"] == packet["canonical_sha256"]
    assert current.artifact_hashes["published_bundle"] == artifact.bundle_sha256
    assert current.artifact_hashes["report_markdown"] == artifact.markdown_sha256
    assert artifact.bundle_sha256 == sha256(canonical_bytes(bundle)).hexdigest()
    assert artifact.markdown_sha256 == sha256(report.encode("utf-8")).hexdigest()


def test_f05_missing_macro_fixture_does_not_create_an_operational_event_calendar(
    tmp_path: Path,
) -> None:
    from finance_research_agent.application.premarket_preparation import prepare_research_packet
    from finance_research_agent.application.publication_service import publish_reduced_report
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )
    from finance_research_agent.evaluation.domain_assertions import execute_domain_assertion
    from finance_research_agent.evaluation.models import FailureInjectionId, FixtureSetId
    from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
    from tests.evaluation.fixture_bank import build_domain_fixture_bank

    scenario = load_evaluation_scenarios()[11]
    assert scenario.id.value == "S12"
    assert scenario.injected_failures == (FailureInjectionId.F05,)
    fixture_bank = build_domain_fixture_bank()
    domain_result = execute_domain_assertion(
        scenario.domain_assertions[0], fixture_bank
    )
    assert domain_result.fixture_id is FixtureSetId.S12_MISSING_MACRO_CALENDAR
    assert domain_result.status == "PASS"
    assert "gate_reason_codes" in domain_result.matched_fields

    deps = dependencies(tmp_path)
    prepared = prepare_research_packet(request(), deps)
    assert prepared.outcome == "PACKET_READY"
    packet = prepared.research_packet
    assert packet is not None
    assert packet.run.data_quality_status is DataQualityStatus.DEGRADED
    assert packet.events == ()
    assert packet.deterministic_plan_inputs == ()
    event_capability = next(
        state
        for state in packet.capability_states
        if state.capability is Capability.EVENT_RISK_CHECK_AVAILABLE
    )
    assert event_capability.available is False
    assert event_capability.reason_codes == (ErrorCode.SOURCE_NOT_CONFIGURED,)
    assert deps.event_providers == ()
    assert deps.market_data.calls == ["instruments", "bars", "prices", "readiness"]

    receipt = publish_reduced_report(
        deps.run_repository,
        packet,
        ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        deps.clock.now_utc(),
    )
    report = deps.run_repository.get_report(receipt.run_id)
    bundle = deps.run_repository.load_published_bundle(receipt.run_id)
    assert report is not None and bundle is not None
    assert "Brief origin: DETERMINISTIC_REDUCED" in report
    assert "EVENT_RISK_CHECK_AVAILABLE" in report
    assert "SOURCE_NOT_CONFIGURED" in report
    assert "earnings" not in report.lower()
    assert receipt.bundle_sha256 == sha256(canonical_bytes(bundle)).hexdigest()
    assert receipt.markdown_sha256 == sha256(report.encode("utf-8")).hexdigest()
    replay = replay_published_artifact(
        deps.run_repository, receipt.run_id, _recorded_versions(bundle)
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f06_synthesis_timeout_publishes_the_same_packet_as_reduced_research(
    tmp_path: Path, valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )
    from finance_research_agent.domain.enums import ReducedReportReason
    from finance_research_agent.evaluation.models import FailureInjectionId
    from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
    from tests.integration.test_premarket_workflow_matrix import _support
    from tests.support.premarket_workflow import run_workflow

    scenario = load_evaluation_scenarios()[22]
    assert scenario.id.value == "S23"
    assert FailureInjectionId.F06 in scenario.injected_failures
    support = _support()
    protocol, packet, _ = support.prepared_protocol(
        tmp_path, valid_packet, valid_brief_draft
    )
    before_packet = canonical_bytes(packet)
    host = support.ScriptedSynthesis([TimeoutError()])

    result = run_workflow(protocol, host)

    assert result.outcome == "deterministic_reduced"
    assert (result.validations, result.repairs) == (0, 0)
    assert protocol.requests[-2].reason is ReducedReportReason.SYNTHESIS_TIMEOUT
    assert protocol.names[-2:] == ("publish_reduced_report", "get_report")
    assert host.packet_objects == [packet]
    assert host.packet_hashes == [packet.canonical_sha256]
    assert canonical_bytes(packet) == before_packet
    assert result.report.bundle["brief_origin"] == BriefOrigin.DETERMINISTIC_REDUCED.value
    assert result.report.bundle["reduced_report_reason"] == (
        ReducedReportReason.SYNTHESIS_TIMEOUT.value
    )
    assert (
        result.report.bundle["research_packet"]["canonical_sha256"]
        == packet.canonical_sha256
    )
    assert protocol.repository.get_report(packet.run.run_id) == result.report.report_markdown
    artifact = protocol.repository.get_published_artifact(packet.run.run_id)
    assert artifact is not None
    assert artifact.bundle_sha256 == sha256(canonical_bytes(result.report)).hexdigest()
    assert artifact.markdown_sha256 == sha256(
        result.report.report_markdown.encode("utf-8")
    ).hexdigest()
    replay = replay_published_artifact(
        protocol.repository,
        packet.run.run_id,
        _recorded_versions(result.report),
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f07_malformed_draft_json_is_not_recorded_as_a_validated_attempt(
    tmp_path: Path, valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )
    from finance_research_agent.domain.enums import ReducedReportReason
    from finance_research_agent.evaluation.models import FailureInjectionId
    from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
    from tests.integration.test_premarket_workflow_matrix import _support
    from tests.support.premarket_workflow import run_workflow

    scenario = load_evaluation_scenarios()[21]
    assert scenario.id.value == "S22"
    assert FailureInjectionId.F07 in scenario.injected_failures
    support = _support()
    protocol, packet, _ = support.prepared_protocol(
        tmp_path, valid_packet, valid_brief_draft
    )
    before_packet = canonical_bytes(packet)
    host = support.ScriptedSynthesis(["{malformed json"])

    result = run_workflow(protocol, host)

    assert result.outcome == "deterministic_reduced"
    assert (result.validations, result.repairs) == (0, 0)
    assert protocol.requests[-2].reason is ReducedReportReason.SYNTHESIS_UNAVAILABLE
    assert protocol.names[-2:] == ("publish_reduced_report", "get_report")
    assert host.packet_objects == [packet]
    assert host.packet_hashes == [packet.canonical_sha256]
    assert canonical_bytes(packet) == before_packet
    assert result.report.bundle["brief_origin"] == BriefOrigin.DETERMINISTIC_REDUCED.value
    assert result.report.bundle["validation_reports"] == ()
    assert (
        result.report.bundle["research_packet"]["canonical_sha256"]
        == packet.canonical_sha256
    )
    artifact = protocol.repository.get_published_artifact(packet.run.run_id)
    assert artifact is not None
    replay = replay_published_artifact(
        protocol.repository,
        packet.run.run_id,
        _recorded_versions(result.report),
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f08_unsupported_claim_is_rejected_before_valid_repair_publishes(
    tmp_path: Path, valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )
    from finance_research_agent.domain.enums import ValidationCode
    from finance_research_agent.evaluation.models import FailureInjectionId
    from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
    from tests.integration.test_premarket_workflow_matrix import _support
    from tests.support.premarket_workflow import run_workflow

    scenario = load_evaluation_scenarios()[21]
    assert scenario.id.value == "S22"
    assert FailureInjectionId.F08 in scenario.injected_failures
    support = _support()
    protocol, packet, _ = support.prepared_protocol(
        tmp_path, valid_packet, valid_brief_draft
    )
    valid_repair = valid_brief_draft.model_copy(
        update={"execution_status": packet.run.execution_status}
    )
    unsupported = valid_repair.model_copy(
        update={"data_warnings": ("unverified earnings are confirmed",)}
    )
    host = support.ScriptedSynthesis([unsupported, valid_repair])
    before_packet = canonical_bytes(packet)

    result = run_workflow(protocol, host)

    assert result.outcome == "published"
    assert (result.validations, result.repairs) == (2, 1)
    assert protocol.names.count("validate_and_publish_brief") == 2
    assert host.packet_objects == [packet, packet]
    assert host.packet_hashes == [packet.canonical_sha256] * 2
    assert canonical_bytes(packet) == before_packet
    assert host.issues[1] is not None
    assert any(issue.code is ValidationCode.UNSUPPORTED_CLAIM for issue in host.issues[1].issues)

    bundle = protocol.repository.load_published_bundle(packet.run.run_id)
    artifact = protocol.repository.get_published_artifact(packet.run.run_id)
    assert bundle is not None and artifact is not None
    assert bundle.bundle["brief_draft"]["data_warnings"] == ()
    final_report = bundle.bundle["validation_report"]
    assert final_report["is_valid"] is True
    assert final_report["validation_attempt"] == 2
    assert artifact.bundle_sha256 == sha256(canonical_bytes(bundle)).hexdigest()
    report = protocol.repository.get_report(packet.run.run_id)
    assert report == result.report.report_markdown
    replay = replay_published_artifact(
        protocol.repository,
        packet.run.run_id,
        _recorded_versions(bundle),
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f09_numeric_mismatch_is_rejected_without_changing_packet_values(
    tmp_path: Path, valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )
    from finance_research_agent.domain.enums import ValidationCode
    from finance_research_agent.evaluation.models import FailureInjectionId
    from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
    from tests.integration.test_premarket_workflow_matrix import _support
    from tests.support.premarket_workflow import run_workflow

    scenario = load_evaluation_scenarios()[21]
    assert scenario.id.value == "S22"
    assert FailureInjectionId.F09 in scenario.injected_failures
    support = _support()
    protocol, packet, _ = support.prepared_protocol(
        tmp_path, valid_packet, valid_brief_draft
    )
    valid_repair = valid_brief_draft.model_copy(
        update={"execution_status": packet.run.execution_status}
    )
    numeric_claim = valid_repair.claims[1].model_copy(
        update={
            "numeric_value": Decimal("104.00"),
            "text": "AAPL two-session SMA is 104.00 price.",
        }
    )
    unsupported = valid_repair.model_copy(
        update={"claims": (valid_repair.claims[0], numeric_claim)}
    )
    host = support.ScriptedSynthesis([unsupported, valid_repair])
    before_packet = canonical_bytes(packet)
    metric = next(
        item
        for item in packet.metrics
        if item.metric_id == valid_repair.claims[1].metric_ids[0]
    )

    result = run_workflow(protocol, host)

    assert result.outcome == "published"
    assert (result.validations, result.repairs) == (2, 1)
    assert host.packet_objects == [packet, packet]
    assert host.packet_hashes == [packet.canonical_sha256] * 2
    assert canonical_bytes(packet) == before_packet
    assert host.issues[1] is not None
    assert any(
        issue.code is ValidationCode.DETERMINISTIC_VALUE_MISMATCH
        for issue in host.issues[1].issues
    )

    bundle = protocol.repository.load_published_bundle(packet.run.run_id)
    assert bundle is not None
    published_claim = next(
        item
        for item in bundle.bundle["brief_draft"]["claims"]
        if item["claim_id"] == valid_repair.claims[1].claim_id
    )
    assert Decimal(published_claim["numeric_value"]) == metric.value
    assert Decimal(published_claim["numeric_value"]) != Decimal("104.00")
    final_report = bundle.bundle["validation_report"]
    assert final_report["is_valid"] is True
    assert final_report["validation_attempt"] == 2
    replay = replay_published_artifact(
        protocol.repository,
        packet.run.run_id,
        _recorded_versions(bundle),
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f10_repair_exhaustion_records_three_validations_and_never_requests_a_fourth(
    tmp_path: Path, valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )
    from finance_research_agent.domain.enums import ValidationCode
    from finance_research_agent.evaluation.models import FailureInjectionId
    from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
    from tests.integration.test_premarket_workflow_matrix import _support
    from tests.support.premarket_workflow import run_workflow

    scenario = load_evaluation_scenarios()[21]
    assert scenario.id.value == "S22"
    assert FailureInjectionId.F10 in scenario.injected_failures
    support = _support()
    protocol, packet, _ = support.prepared_protocol(
        tmp_path, valid_packet, valid_brief_draft
    )
    invalid_drafts = tuple(
        valid_brief_draft.model_copy(
            update={
                "execution_status": packet.run.execution_status,
                "data_warnings": (f"unverified earnings are confirmed attempt-{index}",),
            }
        )
        for index in range(3)
    )
    repaired = valid_brief_draft.model_copy(
        update={"execution_status": packet.run.execution_status}
    )
    host = support.ScriptedSynthesis([*invalid_drafts, repaired])
    before_packet = canonical_bytes(packet)

    result = run_workflow(protocol, host)

    assert result.outcome == "deterministic_reduced"
    assert (result.validations, result.repairs) == (3, 2)
    assert protocol.names.count("validate_and_publish_brief") == 3
    assert protocol.names[-2:] == ("publish_reduced_report", "get_report")
    assert len(host.packet_objects) == 3
    assert host.packet_objects == [packet, packet, packet]
    assert host.packet_hashes == [packet.canonical_sha256] * 3
    assert canonical_bytes(packet) == before_packet
    assert len(host.issues) == 3
    assert host.issues[1] is not None and host.issues[2] is not None
    assert all(
        any(issue.code is ValidationCode.UNSUPPORTED_CLAIM for issue in report.issues)
        for report in host.issues[1:]
        if report is not None
    )

    bundle = protocol.repository.load_published_bundle(packet.run.run_id)
    artifact = protocol.repository.get_published_artifact(packet.run.run_id)
    assert bundle is not None and artifact is not None
    assert bundle.bundle["brief_origin"] == BriefOrigin.DETERMINISTIC_REDUCED.value
    assert bundle.bundle["reduced_report_reason"] == (
        ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED.value
    )
    reports = bundle.bundle["validation_reports"]
    assert len(reports) == 3
    assert [item["validation_attempt"] for item in reports] == [1, 2, 3]
    assert all(item["is_valid"] is False for item in reports)
    assert artifact.bundle_sha256 == sha256(canonical_bytes(bundle)).hexdigest()
    report_markdown = protocol.repository.get_report(packet.run.run_id)
    assert report_markdown == result.report.report_markdown
    replay = replay_published_artifact(
        protocol.repository,
        packet.run.run_id,
        _recorded_versions(bundle),
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


@pytest.mark.parametrize("failure_stage", ["write", "fsync"])
def test_f11_pre_rename_disk_failure_is_typed_and_never_visible_as_published(
    tmp_path: Path, valid_packet, valid_brief_draft, monkeypatch, failure_stage: str
) -> None:
    from finance_research_agent.adapters.filesystem import PublicationError
    from finance_research_agent.application.operations import ProductAOperation
    from tests.integration.test_premarket_workflow_matrix import _support

    protocol, packet, _ = _support().prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    draft = valid_brief_draft.model_copy(
        update={"execution_status": packet.run.execution_status}
    )
    if failure_stage == "write":
        original_atomic_write = protocol.repository._atomic_write

        def fail_bundle_write(target: Path, payload: bytes) -> None:
            if target.name == "bundle.json":
                raise OSError("injected publication bundle write failure")
            original_atomic_write(target, payload)

        monkeypatch.setattr(protocol.repository, "_atomic_write", fail_bundle_write)
    else:
        original_fsync_directory = protocol.repository._fsync_directory

        def fail_staging_fsync(directory: Path) -> None:
            if directory.name == packet.run.run_id:
                raise OSError("injected staging directory fsync failure")
            original_fsync_directory(directory)

        monkeypatch.setattr(protocol.repository, "_fsync_directory", fail_staging_fsync)

    with pytest.raises(PublicationError) as raised:
        protocol.dispatch(
            ProductAOperation.VALIDATE_AND_PUBLISH_BRIEF.value,
            json.dumps({"draft": draft.model_dump(mode="json")}),
        )

    assert raised.value.code is ErrorCode.PUBLICATION_FAILED
    assert protocol.repository.get_latest(packet.run.market_date) is None
    assert protocol.repository.get_published_artifact(packet.run.run_id) is None
    assert protocol.repository.load_published_bundle(packet.run.run_id) is None
    assert protocol.repository.get_report(packet.run.run_id) is None
    assert protocol.repository.diagnostic_staging_exists(packet.run.run_id)
    stored = protocol.repository.load(packet.run.run_id)
    assert stored is not None and stored.published is False
    assert stored.checkpoints[-1].stage == "PUBLISHED"
    final_run = (
        tmp_path
        / "runs"
        / str(packet.run.market_date.year)
        / packet.run.market_date.isoformat()
        / packet.run.run_id
    )
    assert final_run.exists() is False


def test_f12_index_interruption_hides_then_recovers_the_same_frozen_publication(
    tmp_path: Path, valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.adapters.filesystem import PublicationError
    from finance_research_agent.application.operations import ProductAOperation
    from finance_research_agent.application.replay_service import (
        _recorded_versions,
        replay_published_artifact,
    )
    from tests.integration.test_premarket_workflow_matrix import _support

    protocol, packet, _ = _support().prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    draft = valid_brief_draft.model_copy(
        update={"execution_status": packet.run.execution_status}
    )
    protocol.repository.inject_failure_during_index_update = True
    arguments = json.dumps({"draft": draft.model_dump(mode="json")})

    with pytest.raises(PublicationError) as raised:
        protocol.dispatch(ProductAOperation.VALIDATE_AND_PUBLISH_BRIEF.value, arguments)

    assert raised.value.code is ErrorCode.PUBLICATION_FAILED
    assert protocol.repository.get_latest(packet.run.market_date) is None
    assert protocol.repository.get_published_artifact(packet.run.run_id) is None
    assert protocol.repository.load_published_bundle(packet.run.run_id) is None
    assert protocol.repository.get_report(packet.run.run_id) is None
    assert protocol.repository.load(packet.run.run_id) is None
    assert protocol.repository.diagnostic_orphan_exists(packet.run.run_id)
    orphan = (
        tmp_path
        / "diagnostics"
        / "orphans"
        / str(packet.run.market_date.year)
        / packet.run.market_date.isoformat()
        / packet.run.run_id
    )
    expected_bundle = (orphan / "bundle.json").read_bytes()
    expected_report = (orphan / "report.md").read_bytes()
    expected_bundle_sha256 = sha256(expected_bundle).hexdigest()
    expected_report_sha256 = sha256(expected_report).hexdigest()

    protocol.repository.inject_failure_during_index_update = False
    receipt = protocol.repository.recover_interrupted_publication(packet.run.run_id)
    assert protocol.repository.recover_interrupted_publication(packet.run.run_id) == receipt

    assert receipt.run_id == packet.run.run_id
    assert receipt.bundle_sha256 == expected_bundle_sha256
    assert receipt.markdown_sha256 == expected_report_sha256
    assert protocol.repository.diagnostic_orphan_exists(packet.run.run_id) is False
    assert protocol.repository.get_latest(packet.run.market_date) == packet.run.run_id
    stored = protocol.repository.load(packet.run.run_id)
    assert stored is not None and stored.published is True
    recovered_bundle = protocol.repository.load_published_bundle(packet.run.run_id)
    assert recovered_bundle is not None
    assert canonical_bytes(recovered_bundle) == expected_bundle
    recovered_report = protocol.repository.get_report(packet.run.run_id)
    assert recovered_report is not None
    assert recovered_report.encode("utf-8") == expected_report
    replay = replay_published_artifact(
        protocol.repository,
        packet.run.run_id,
        _recorded_versions(recovered_bundle),
    )
    assert replay.json_matches is True
    assert replay.markdown_matches is True


def test_f12_recovery_rejects_an_orphan_with_a_changed_staged_artifact(
    tmp_path: Path, valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.adapters.filesystem import PublicationError
    from finance_research_agent.application.operations import ProductAOperation
    from tests.integration.test_premarket_workflow_matrix import _support

    protocol, packet, _ = _support().prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    draft = valid_brief_draft.model_copy(
        update={"execution_status": packet.run.execution_status}
    )
    protocol.repository.inject_failure_during_index_update = True
    with pytest.raises(PublicationError):
        protocol.dispatch(
            ProductAOperation.VALIDATE_AND_PUBLISH_BRIEF.value,
            json.dumps({"draft": draft.model_dump(mode="json")}),
        )

    orphan_artifact = (
        tmp_path
        / "diagnostics"
        / "orphans"
        / str(packet.run.market_date.year)
        / packet.run.market_date.isoformat()
        / packet.run.run_id
        / "artifacts"
        / "research_packet.bin"
    )
    assert orphan_artifact.is_file()
    orphan_artifact.write_bytes(b"changed frozen packet bytes")
    protocol.repository.inject_failure_during_index_update = False

    with pytest.raises(PublicationError, match="artifact hash differs"):
        protocol.repository.recover_interrupted_publication(packet.run.run_id)

    assert protocol.repository.diagnostic_orphan_exists(packet.run.run_id)
    assert protocol.repository.get_latest(packet.run.market_date) is None
    assert protocol.repository.load_published_bundle(packet.run.run_id) is None
    assert protocol.repository.get_published_artifact(packet.run.run_id) is None
