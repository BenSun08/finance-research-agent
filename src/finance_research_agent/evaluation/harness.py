"""Finite current-scope evaluation harness over production application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import cast

from finance_research_agent.application.premarket_preparation import (
    PreparedPremarketRunResult,
    prepare_research_packet,
)
from finance_research_agent.application.publication_service import (
    PublicationRepository,
    publish_reduced_report,
)
from finance_research_agent.application.replay_service import (
    _recorded_versions,
    replay_published_artifact,
)
from finance_research_agent.application.run_service import (
    PreparePremarketRunRequest,
    RunDependencies,
)
from finance_research_agent.domain.enums import (
    Capability,
    InvocationType,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import CapabilityState
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.evaluation.models import (
    CurrentScopeServiceObservation,
    EvaluationScenario,
    ScenarioOutcomeExpectation,
)


@dataclass(frozen=True, slots=True)
class EvaluationHarness:
    """Injected offline application dependencies for one isolated scenario run."""

    dependencies_factory: Callable[[], RunDependencies]
    market_date: date
    reduced_report_reason: ReducedReportReason

    def __post_init__(self) -> None:
        if type(self.market_date) is not date:
            raise TypeError("market_date must be a date")


def execute_current_scope_scenario(
    scenario: EvaluationScenario,
    harness: EvaluationHarness,
) -> CurrentScopeServiceObservation:
    """Prepare, reduce, publish, read, and replay one offline scenario run."""

    scenario = EvaluationScenario.model_validate(scenario, strict=True)
    dependencies = harness.dependencies_factory()
    if type(dependencies) is not RunDependencies:
        raise TypeError("dependencies_factory must return RunDependencies")
    prepared = prepare_research_packet(
        PreparePremarketRunRequest(
            market_date=harness.market_date,
            requested_revision=None,
            invocation=InvocationType.SCHEDULED,
        ),
        dependencies,
    )
    packet = _require_packet(prepared)
    repository = cast(PublicationRepository, dependencies.run_repository)
    receipt = publish_reduced_report(
        repository,
        packet,
        harness.reduced_report_reason,
        dependencies.clock.now_utc(),
    )
    if receipt.run_id != packet.run.run_id:
        raise ValueError("publication receipt differs from the prepared scenario run")
    bundle = repository.load_published_bundle(packet.run.run_id)
    report = repository.get_report(packet.run.run_id)
    if bundle is None or report is None or report != bundle.report_markdown:
        raise ValueError("published scenario artifacts could not be read consistently")
    replay = replay_published_artifact(
        repository,
        packet.run.run_id,
        _recorded_versions(bundle),
    )
    banner_lines = tuple(
        line for line in report.splitlines() if line.startswith("Brief origin: ")
    )
    if len(banner_lines) != 1:
        raise ValueError("published scenario report requires one explicit brief-origin banner")
    capabilities = _ordered_capabilities(packet)
    failure_code = bundle.bundle.get("failure_code")
    if failure_code is None:
        error_codes: tuple[ErrorCode, ...] = ()
    elif isinstance(failure_code, str):
        error_codes = (ErrorCode(failure_code),)
    else:
        raise ValueError("published scenario failure_code is not a stable string")
    expected_case_id = scenario.current_scope_expectation.primary.case_id
    observed = ScenarioOutcomeExpectation(
        case_id=expected_case_id,
        execution_status=bundle.run.execution_status,
        data_quality_status=bundle.run.data_quality_status,
        delivery_status=bundle.run.delivery_status,
        capabilities=capabilities,
        plan_states=tuple(plan.plan_status for plan in packet.deterministic_plan_inputs),
        report_banner=banner_lines[0],
        error_codes=error_codes,
        recoverability=replay.json_matches and replay.markdown_matches,
    )
    if receipt.bundle_sha256 != replay.stored_json_sha256:
        raise ValueError("publication receipt bundle hash differs from replay evidence")
    if receipt.markdown_sha256 != replay.stored_markdown_sha256:
        raise ValueError("publication receipt report hash differs from replay evidence")
    artifact_hashes = {
        "research_packet": packet.canonical_sha256,
        "published_bundle": receipt.bundle_sha256,
        "report_markdown": receipt.markdown_sha256,
        "replayed_bundle": replay.replayed_json_sha256,
    }
    if replay.replayed_markdown_sha256 is not None:
        artifact_hashes["replayed_markdown"] = replay.replayed_markdown_sha256
    return CurrentScopeServiceObservation(
        scenario_id=scenario.id,
        current_scope=observed,
        artifact_hashes=FrozenMap(artifact_hashes),
        replay_json_matches=replay.json_matches,
        replay_markdown_matches=replay.markdown_matches,
    )


def _require_packet(prepared: PreparedPremarketRunResult) -> ResearchPacket:
    if prepared.outcome != "PACKET_READY" or prepared.research_packet is None:
        raise RuntimeError(
            "current-scope scenario service run did not produce a frozen research packet"
        )
    return prepared.research_packet


def _ordered_capabilities(packet: ResearchPacket) -> tuple[CapabilityState, ...]:
    states = {state.capability: state for state in packet.capability_states}
    if set(states) != set(Capability):
        raise ValueError("prepared packet does not contain the exact capability set")
    return tuple(states[capability] for capability in Capability)
