"""Map frozen market collection failures into deterministic Product A quality."""

from collections.abc import Sequence

from finance_research_agent.application.market_collection import MarketDataCollection
from finance_research_agent.domain.models import (
    ProviderFailure,
    SourceHealth,
)
from finance_research_agent.domain.policies import RiskPolicy
from finance_research_agent.domain.quality import DataQualityResult, evaluate_data_quality


def evaluate_collected_market_data_quality(
    collection: MarketDataCollection,
    *,
    source_health: Sequence[SourceHealth],
    risk_policy: RiskPolicy | None,
) -> DataQualityResult:
    """Evaluate failures without losing their global, symbol, or price scope."""
    if not isinstance(collection, MarketDataCollection):
        raise TypeError("collection must be MarketDataCollection")
    symbols = tuple(result.symbol for result in collection.symbols)
    if len(set(symbols)) != len(symbols):
        raise ValueError("market-data collection symbols must be unique")

    provider_failures: list[ProviderFailure] = []
    current_price_failures: list[ProviderFailure] = []
    for result in collection.symbols:
        for outcome in (result.instrument, result.daily_bars):
            if isinstance(outcome, ProviderFailure):
                if outcome.symbol not in (None, result.symbol):
                    raise ValueError("provider failure symbol differs from collection symbol")
                provider_failures.append(outcome)
        price = result.premarket_observation
        if isinstance(price, ProviderFailure):
            if price.symbol not in (None, result.symbol):
                raise ValueError("provider failure symbol differs from collection symbol")
            if price.symbol is None:
                provider_failures.append(price)
            else:
                current_price_failures.append(price)

    return evaluate_data_quality(
        source_health=source_health,
        provider_failures=provider_failures,
        current_price_failures=current_price_failures,
        risk_policy=risk_policy,
    )
