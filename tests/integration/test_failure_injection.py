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
