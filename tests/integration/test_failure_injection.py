"""R11 failure injections against real application and repository services."""

from datetime import date
from hashlib import sha256
from pathlib import Path

from finance_research_agent.domain.enums import (
    BriefOrigin,
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    PlanStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import ProviderFailure
from finance_research_agent.domain.types import canonical_bytes
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
