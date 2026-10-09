"""Preparation observes immutable prior hypotheses through the last trading close."""

from dataclasses import replace

from finance_research_agent.application.premarket_preparation import prepare_research_packet
from tests.application.test_premarket_preparation import dependencies, request
from tests.application.test_prior_research import _publication
from tests.application.test_prior_research import prior_packet as prior_packet

pytest_plugins = ("tests.unit.test_trade_plan",)


def test_monday_preparation_retains_real_prior_observation_and_reference(tmp_path, prior_packet):
    deps = dependencies(tmp_path)
    _publication(deps.run_repository, prior_packet)
    plan = prior_packet.deterministic_plan_inputs[0]
    configuration = deps.config_repository.load()

    class PriorWatchlist:
        def load(self):
            return configuration.model_copy(
                update={
                    "watchlist": configuration.watchlist.model_copy(
                        update={
                            "items": (
                                configuration.watchlist.items[0].model_copy(
                                    update={"symbol": plan.symbol}
                                ),
                            )
                        }
                    )
                }
            )

    deps = replace(deps, config_repository=PriorWatchlist())
    original_instruments = deps.market_data.fetch_instruments
    original_bars = deps.market_data.fetch_daily_bars
    original_prices = deps.market_data.fetch_premarket_observations

    def instruments(*args, **kwargs):
        return {
            symbol: item.model_copy(
                update={
                    "instrument_id": plan.entry_zone.upper.instrument_id,
                }
            )
            if symbol == plan.symbol
            else item
            for symbol, item in original_instruments(*args, **kwargs).items()
        }

    def bars(*args, **kwargs):
        return {
            symbol: tuple(
                bar.model_copy(
                    update={
                        "instrument_id": plan.entry_zone.upper.instrument_id,
                    }
                )
                for bar in values
            )
            if symbol == plan.symbol
            else values
            for symbol, values in original_bars(*args, **kwargs).items()
        }

    def prices(*args, **kwargs):
        return {
            symbol: item.model_copy(
                update={
                    "instrument_id": plan.entry_zone.upper.instrument_id,
                }
            )
            if symbol == plan.symbol
            else item
            for symbol, item in original_prices(*args, **kwargs).items()
        }

    deps.market_data.fetch_instruments = instruments
    deps.market_data.fetch_daily_bars = bars
    deps.market_data.fetch_premarket_observations = prices
    result = prepare_research_packet(request(), deps)
    (observation,) = result.research_packet.prior_plan_observations
    assert observation.plan_id == plan.plan_id
    assert observation.observed_through.isoformat() == "2026-09-25"
    assert observation.observation_reference_evidence_id in {
        item.evidence_id for item in result.research_packet.evidence
    }
    assert result.research_packet.deterministic_plan_inputs == ()
