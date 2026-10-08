from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    SymbolMarketCollection,
)
from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
)

_RETRIEVED = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
_OBSERVED = _RETRIEVED - timedelta(minutes=1)


def _instrument(symbol: str = "AAPL") -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id=f"NASDAQ:{symbol}",
        symbol=symbol,
        name="Apple Inc." if symbol == "AAPL" else symbol,
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )


def _bar() -> CompletedDailyBar:
    return CompletedDailyBar(
        instrument_id="NASDAQ:AAPL",
        session_date=date(2026, 9, 25),
        source_timestamp=_OBSERVED,
        open=Decimal("100"),
        high=Decimal("104"),
        low=Decimal("99"),
        close=Decimal("103"),
        volume=1000,
        session=Session.COMPLETED_SESSION,
        provider="alpaca",
        feed="iex",
        coverage=Coverage.SINGLE_EXCHANGE,
        adjustment="SPLIT",
        retrieved_at=_RETRIEVED,
        evidence_cutoff_at=_RETRIEVED,
        evidence_id="provider-bar-evidence",
        quality_flags=(),
    )


def _price() -> PriceObservation:
    return PriceObservation(
        instrument_id="NASDAQ:AAPL",
        value=Decimal("104.25"),
        currency="USD",
        session=Session.PRE_MARKET,
        provider="alpaca",
        feed="iex",
        coverage=Coverage.SINGLE_EXCHANGE,
        observed_at=_OBSERVED,
        retrieved_at=_RETRIEVED,
        evidence_id="provider-price-evidence",
        quality_flags=(),
    )


def test_collected_snapshot_projects_regime_without_historical_id_convention() -> None:
    from finance_research_agent.application.collection_bridge import (
        collected_snapshot_to_regime_input,
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(SymbolMarketCollection("AAPL", _instrument(), (_bar(),), _price()),),
        completed_at=_RETRIEVED,
    )
    canonical = collection_to_packet_inputs(collection, authority_tier=2)
    result = collected_snapshot_to_regime_input(canonical.market["AAPL"], _RETRIEVED)
    assert result.symbol == "AAPL"
    assert result.as_of == _RETRIEVED
    assert result.completed_daily_bars[0].close == Decimal("103")
    assert result.completed_daily_bars[0].volume == 1000
    assert result.input_evidence_ids == (
        canonical.market["AAPL"].completed_daily_bars[0].evidence_id,
    )
    assert result.snapshot_id.startswith("normalized-")
    changed = canonical.market["AAPL"].model_copy(
        update={"completed_daily_bars": (_bar().model_copy(update={"close": Decimal("102")}),)}
    )
    assert collected_snapshot_to_regime_input(changed, _RETRIEVED).snapshot_id != result.snapshot_id


@pytest.mark.parametrize("invalid", ["empty", "late", "mismatched_identity"])
def test_collected_regime_projection_rejects_invalid_or_post_cutoff_bars(invalid) -> None:
    from finance_research_agent.application.collection_bridge import (
        collected_snapshot_to_regime_input,
        collection_to_packet_inputs,
    )

    canonical = collection_to_packet_inputs(
        MarketDataCollection(
            (SymbolMarketCollection("AAPL", _instrument(), (_bar(),), _price()),),
            _RETRIEVED,
        ),
        authority_tier=2,
    ).market["AAPL"]
    updates = {
        "empty": {"completed_daily_bars": ()},
        "late": {"completed_daily_bars": (_bar().model_copy(
            update={"retrieved_at": _RETRIEVED + timedelta(seconds=1)}
        ),)},
        "mismatched_identity": {"instrument": _instrument("MSFT")},
    }
    with pytest.raises(ValueError):
        collected_snapshot_to_regime_input(
            canonical.model_copy(update=updates[invalid]), _RETRIEVED
        )


@pytest.mark.parametrize("invalid", ["source", "bar_cutoff", "naive_cutoff", "non_utc_cutoff"])
def test_collected_regime_projection_enforces_source_and_cutoff_boundaries(invalid) -> None:
    from datetime import timezone

    from finance_research_agent.application.collection_bridge import (
        collected_snapshot_to_regime_input,
        collection_to_packet_inputs,
    )

    snapshot = collection_to_packet_inputs(
        MarketDataCollection(
            (SymbolMarketCollection("AAPL", _instrument(), (_bar(),), _price()),),
            _RETRIEVED,
        ), authority_tier=2,
    ).market["AAPL"]
    cutoff = _RETRIEVED
    if invalid == "source":
        snapshot = snapshot.model_copy(update={"source_observations": (
            snapshot.source_observations[0].model_copy(
                update={"retrieved_at": _RETRIEVED + timedelta(seconds=1)}
            ),
        )})
    elif invalid == "bar_cutoff":
        snapshot = snapshot.model_copy(update={"completed_daily_bars": (
            _bar().model_copy(update={"evidence_cutoff_at": _RETRIEVED + timedelta(seconds=1)}),
        )})
    elif invalid == "naive_cutoff":
        cutoff = cutoff.replace(tzinfo=None)
    else:
        cutoff = cutoff.astimezone(timezone(timedelta(hours=1)))
    with pytest.raises(ValueError):
        collected_snapshot_to_regime_input(snapshot, cutoff)


def test_regime_projection_identity_includes_frozen_cutoff() -> None:
    from finance_research_agent.application.collection_bridge import (
        collected_snapshot_to_regime_input,
        collection_to_packet_inputs,
    )

    snapshot = collection_to_packet_inputs(
        MarketDataCollection(
            (SymbolMarketCollection("AAPL", _instrument(), (_bar(),), _price()),),
            _RETRIEVED,
        ), authority_tier=2,
    ).market["AAPL"]
    first = collected_snapshot_to_regime_input(snapshot, _RETRIEVED)
    later = collected_snapshot_to_regime_input(snapshot, _RETRIEVED + timedelta(seconds=1))
    assert first.snapshot_id != later.snapshot_id
    assert first.completed_daily_bars == later.completed_daily_bars


def test_collection_bridge_builds_linked_canonical_market_and_evidence() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=(_bar(),),
                premarket_observation=_price(),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    result = collection_to_packet_inputs(collection, authority_tier=2)

    snapshot = result.market["AAPL"]
    assert snapshot.instrument == _instrument()
    assert len(snapshot.completed_daily_bars) == 1
    assert snapshot.latest_price is not None
    assert {item.evidence_id for item in result.evidence} == {
        snapshot.completed_daily_bars[0].evidence_id,
        snapshot.latest_price.evidence_id,
    }
    assert {item.source.observation_id for item in result.evidence} == {
        source.observation_id for source in snapshot.source_observations
    }
    assert all(item.authority_tier == 2 for item in result.evidence)
    assert result.evidence[0].source.retrieved_at <= collection.completed_at


def test_collection_bridge_is_deterministic_and_preserves_source_provenance() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=(_bar(),),
                premarket_observation=_price(),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    first = collection_to_packet_inputs(collection, authority_tier=2)
    second = collection_to_packet_inputs(collection, authority_tier=2)

    assert first == second
    assert {source.provider for source in first.market["AAPL"].source_observations} == {
        "alpaca"
    }
    assert all(
        source.observed_at == _OBSERVED and source.retrieved_at == _RETRIEVED
        for source in first.market["AAPL"].source_observations
    )


def test_collection_bridge_preserves_original_daily_bar_evidence_ids() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    earlier = _bar().model_copy(
        update={"session_date": date(2026, 9, 24), "evidence_id": "provider-bar-older"}
    )
    later = _bar().model_copy(update={"evidence_id": "provider-bar-newer"})
    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=(later, earlier),
                premarket_observation=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    result = collection_to_packet_inputs(collection, authority_tier=2)
    bar_evidence = next(
        item for item in result.evidence if item.citation_label == "AAPL completed daily bars"
    )

    assert bar_evidence.structured_fields["source_evidence_ids"] == (
        "provider-bar-older",
        "provider-bar-newer",
    )


def test_collection_bridge_preserves_original_price_evidence_id() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                premarket_observation=_price(),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    result = collection_to_packet_inputs(collection, authority_tier=2)
    price_evidence = next(
        item
        for item in result.evidence
        if item.citation_label == "AAPL premarket price observation"
    )

    assert price_evidence.structured_fields["source_evidence_id"] == "provider-price-evidence"


def test_collection_bridge_rejects_daily_bars_from_mixed_providers() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    mixed_provider_bar = _bar().model_copy(
        update={"session_date": date(2026, 9, 24), "provider": "other-provider"}
    )
    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=(_bar(), mixed_provider_bar),
                premarket_observation=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    with pytest.raises(ValueError, match="daily bars contain multiple providers"):
        collection_to_packet_inputs(collection, authority_tier=2)


def test_collection_bridge_rejects_conflicting_data_identities_without_instrument() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                daily_bars=(_bar(),),
                premarket_observation=_price().model_copy(
                    update={"instrument_id": "NASDAQ:MSFT"}
                ),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    with pytest.raises(ValueError, match="market outcomes contain multiple instrument identities"):
        collection_to_packet_inputs(collection, authority_tier=2)


def test_collection_bridge_rejects_daily_bar_retrieval_after_completion() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    late_bar = _bar().model_copy(update={"retrieved_at": _RETRIEVED + timedelta(seconds=1)})
    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=(late_bar,),
                premarket_observation=_price(),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    with pytest.raises(ValueError, match="daily-bar retrieval is after collection completion"):
        collection_to_packet_inputs(collection, authority_tier=2)


def test_collection_bridge_rejects_price_retrieval_after_completion() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    late_price = _price().model_copy(update={"retrieved_at": _RETRIEVED + timedelta(seconds=1)})
    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                premarket_observation=late_price,
            ),
        ),
        completed_at=_RETRIEVED,
    )

    with pytest.raises(ValueError, match="price retrieval is after collection completion"):
        collection_to_packet_inputs(collection, authority_tier=2)


def test_global_provider_failure_remains_globally_scoped_in_evidence() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=ProviderFailure(
                    provider="alpaca",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                premarket_observation=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    result = collection_to_packet_inputs(collection, authority_tier=2)
    global_failure = next(
        item for item in result.evidence if item.structured_fields["outcome"] == "DAILY_BARS"
    )

    assert global_failure.instrument_id is None
    assert global_failure.structured_fields["scope"] == "global"
    assert global_failure.structured_fields["requested_symbol"] == "AAPL"


def test_collection_bridge_records_failure_codes_without_provider_message() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    failure = ProviderFailure(
        provider="alpaca",
        symbol="AAPL",
        error_code=ErrorCode.PROVIDER_UNAVAILABLE,
        retryable=True,
        message="sensitive transport detail",
    )
    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=failure,
                premarket_observation=_price(),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    result = collection_to_packet_inputs(collection, authority_tier=2)

    failed = next(item for item in result.evidence if "failure" in item.citation_label)
    assert failed.structured_fields["error_code"] == ErrorCode.PROVIDER_UNAVAILABLE.value
    assert "message" not in failed.structured_fields
    assert "sensitive transport detail" not in repr(failed.model_dump(mode="json"))
    assert failed.source.observed_at == _RETRIEVED
    assert failed.source.retrieved_at == _RETRIEVED


def test_collection_bridge_omits_snapshot_without_instrument_identity() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                daily_bars=(),
                premarket_observation=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    result = collection_to_packet_inputs(collection, authority_tier=2)

    assert not result.market
    assert len(result.evidence) == 2
    assert {item.instrument_id for item in result.evidence} == {"AAPL"}


def test_collection_bridge_keeps_other_symbol_evidence_when_identity_fails() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=ProviderFailure(
                    provider="alpaca",
                    symbol="AAPL",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                daily_bars=(_bar(),),
                premarket_observation=_price(),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    result = collection_to_packet_inputs(collection, authority_tier=2)

    assert not result.market
    assert len(result.evidence) == 3
    assert {item.instrument_id for item in result.evidence} == {"AAPL", "NASDAQ:AAPL"}


def test_collection_bridge_rejects_duplicate_symbols_and_invalid_authority(
) -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    entry = SymbolMarketCollection(
        symbol="AAPL",
        instrument=_instrument(),
        daily_bars=(),
        premarket_observation=_price(),
    )
    duplicate = MarketDataCollection(symbols=(entry, entry), completed_at=_RETRIEVED)

    with pytest.raises(ValueError, match="symbols must be unique"):
        collection_to_packet_inputs(duplicate, authority_tier=2)
    with pytest.raises(ValueError, match="authority_tier"):
        collection_to_packet_inputs(
            MarketDataCollection(symbols=(entry,), completed_at=_RETRIEVED),
            authority_tier=0,
        )


def test_collection_bridge_rejects_failure_for_a_different_symbol() -> None:
    from finance_research_agent.application.collection_bridge import (
        collection_to_packet_inputs,
    )

    collection = MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=_instrument(),
                daily_bars=ProviderFailure(
                    provider="alpaca",
                    symbol="MSFT",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                premarket_observation=_price(),
            ),
        ),
        completed_at=_RETRIEVED,
    )

    with pytest.raises(ValueError, match="failure symbol differs"):
        collection_to_packet_inputs(collection, authority_tier=2)
