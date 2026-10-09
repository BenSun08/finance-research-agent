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
from finance_research_agent.application.report_renderer import _inline_text
from finance_research_agent.application.run_service import (
    PreparePremarketRunRequest,
    RunDependencies,
)
from finance_research_agent.domain.enums import (
    Capability,
    DataQualityStatus,
    InvocationType,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import CapabilityState
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.quality import DataQualityResult
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.evaluation.domain_assertions import (
    DomainFixtureBank,
    execute_domain_assertion,
)
from finance_research_agent.evaluation.models import (
    CurrentScopeServiceObservation,
    DomainAssertionOutcome,
    EvaluationOutcome,
    EvaluationScenario,
    ScenarioAssertionId,
    ScenarioOutcomeExpectation,
)


@dataclass(frozen=True, slots=True)
class EvaluationHarness:
    """Injected offline application dependencies for one isolated scenario run."""

    dependencies_factory: Callable[
        [EvaluationScenario, ScenarioOutcomeExpectation], RunDependencies
    ]
    market_date: date
    reduced_report_reason: ReducedReportReason
    domain_fixtures: DomainFixtureBank | None = None

    def __post_init__(self) -> None:
        if type(self.market_date) is not date:
            raise TypeError("market_date must be a date")


def execute_current_scope_scenario(
    scenario: EvaluationScenario,
    harness: EvaluationHarness,
    expectation: ScenarioOutcomeExpectation | None = None,
) -> CurrentScopeServiceObservation:
    """Prepare, reduce, publish, read, and replay one offline scenario run."""

    scenario = EvaluationScenario.model_validate(scenario, strict=True)
    selected_expectation = expectation or scenario.current_scope_expectation.primary
    selected_expectation = ScenarioOutcomeExpectation.model_validate(
        selected_expectation, strict=True
    )
    dependencies = harness.dependencies_factory(scenario, selected_expectation)
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
    repository = cast(PublicationRepository, dependencies.run_repository)
    packet: ResearchPacket | None
    if prepared.outcome == "PACKET_READY":
        packet = _require_packet(prepared)
        publish_reduced_report(
            repository,
            packet,
            harness.reduced_report_reason,
            dependencies.clock.now_utc(),
        )
        run_id = packet.run.run_id
    elif prepared.outcome == "PUBLISHED" and prepared.stored_run is not None:
        packet = None
        run_id = prepared.stored_run.run_id
    else:
        raise RuntimeError("current-scope scenario requires a publishable or skipped outcome")
    return _observe_published_scenario(
        scenario,
        selected_expectation,
        dependencies,
        repository,
        run_id,
        packet,
        harness.market_date,
        prepared.data_quality,
    )


def _observe_published_scenario(
    scenario: EvaluationScenario,
    selected_expectation: ScenarioOutcomeExpectation,
    dependencies: RunDependencies,
    repository: PublicationRepository,
    run_id: str,
    packet: ResearchPacket | None,
    market_date: date,
    data_quality: DataQualityResult | None,
) -> CurrentScopeServiceObservation:
    receipt = repository.get_published_artifact(run_id)
    if receipt is None:
        raise ValueError("published scenario run is missing its verified receipt")
    if receipt.run_id != run_id:
        raise ValueError("publication receipt differs from the scenario run")
    bundle = repository.load_published_bundle(run_id)
    report = repository.get_report(run_id)
    if bundle is None or report is None or report != bundle.report_markdown:
        raise ValueError("published scenario artifacts could not be read consistently")
    replay = replay_published_artifact(
        repository,
        run_id,
        _recorded_versions(bundle),
    )
    banner_lines = tuple(
        line for line in report.splitlines() if line.startswith("Brief origin: ")
    )
    if len(banner_lines) != 1:
        raise ValueError("published scenario report requires one explicit brief-origin banner")
    capabilities = (
        _ordered_capabilities(packet)
        if packet is not None
        else _operational_capabilities(
            bundle.run.data_quality_status,
            bundle.bundle.get("failure_code"),
            data_quality,
        )
    )
    failure_code = bundle.bundle.get("failure_code")
    if failure_code is None:
        error_codes: tuple[ErrorCode, ...] = ()
    elif isinstance(failure_code, str):
        error_codes = (ErrorCode(failure_code),)
    else:
        raise ValueError("published scenario failure_code is not a stable string")
    observed = ScenarioOutcomeExpectation(
        case_id=selected_expectation.case_id,
        execution_status=bundle.run.execution_status,
        data_quality_status=bundle.run.data_quality_status,
        delivery_status=bundle.run.delivery_status,
        capabilities=capabilities,
        plan_states=(
            tuple(plan.plan_status for plan in packet.deterministic_plan_inputs)
            if packet is not None
            else ()
        ),
        report_banner=banner_lines[0],
        error_codes=error_codes,
        recoverability=replay.json_matches and replay.markdown_matches,
    )
    if receipt.bundle_sha256 != replay.stored_json_sha256:
        raise ValueError("publication receipt bundle hash differs from replay evidence")
    if receipt.markdown_sha256 != replay.stored_markdown_sha256:
        raise ValueError("publication receipt report hash differs from replay evidence")
    artifact_hashes = {
        "published_bundle": receipt.bundle_sha256,
        "report_markdown": receipt.markdown_sha256,
        "replayed_bundle": replay.replayed_json_sha256,
    }
    if packet is not None:
        artifact_hashes["research_packet"] = packet.canonical_sha256
    if replay.replayed_markdown_sha256 is not None:
        artifact_hashes["replayed_markdown"] = replay.replayed_markdown_sha256
    return CurrentScopeServiceObservation(
        scenario_id=scenario.id,
        current_scope=observed,
        artifact_hashes=FrozenMap(artifact_hashes),
        replay_json_matches=replay.json_matches,
        replay_markdown_matches=replay.markdown_matches,
        source_limitations_adjacent=(
            _source_limitations_adjacent(packet, report) if packet is not None else False
        ),
        watchlist_exclusions_visible=(
            _watchlist_exclusions_visible(packet, report)
            if packet is not None
            else "No market conclusion or trade plan is available." in report
        ),
        provider_call_count=_provider_call_count(dependencies),
        missed_run_record_durable=repository.get_missed_run(market_date) is not None,
    )


def execute_evaluation_scenario(
    scenario: EvaluationScenario,
    harness: EvaluationHarness,
) -> EvaluationOutcome:
    """Run every declared current-scope case and typed domain assertion."""

    scenario = EvaluationScenario.model_validate(scenario, strict=True)
    expectations = (
        scenario.current_scope_expectation.primary,
        *scenario.current_scope_expectation.subcases,
    )
    if scenario.domain_assertions and harness.domain_fixtures is None:
        raise ValueError("scenario domain assertions require an explicit DomainFixtureBank")
    current_scope_outcomes = tuple(
        execute_current_scope_scenario(scenario, harness, expectation)
        for expectation in expectations
    )
    domain_outcomes: tuple[DomainAssertionOutcome, ...]
    if harness.domain_fixtures is None:
        domain_outcomes = ()
    else:
        domain_outcomes = tuple(
            execute_domain_assertion(assertion, harness.domain_fixtures)
            for assertion in scenario.domain_assertions
        )
    passed: list[ScenarioAssertionId] = []
    failed: list[ScenarioAssertionId] = []
    pending: list[ScenarioAssertionId] = []
    declared = set(scenario.assertions)
    if ScenarioAssertionId.CURRENT_SCOPE_MATCHES in declared:
        matches = all(
            actual.current_scope == expected
            for actual, expected in zip(current_scope_outcomes, expectations, strict=True)
        )
        (passed if matches else failed).append(ScenarioAssertionId.CURRENT_SCOPE_MATCHES)
    if ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS in declared:
        if not scenario.domain_assertions or len(domain_outcomes) != len(
            scenario.domain_assertions
        ):
            pending.append(ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS)
        elif all(outcome.status == "PASS" for outcome in domain_outcomes):
            passed.append(ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS)
        else:
            failed.append(ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS)
    if ScenarioAssertionId.SOURCE_LIMITATIONS_ADJACENT in declared:
        matches = all(
            outcome.source_limitations_adjacent for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(ScenarioAssertionId.SOURCE_LIMITATIONS_ADJACENT)
    if ScenarioAssertionId.ALL_WATCHLIST_EXCLUSIONS_VISIBLE in declared:
        matches = all(
            outcome.watchlist_exclusions_visible for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(
            ScenarioAssertionId.ALL_WATCHLIST_EXCLUSIONS_VISIBLE
        )
    completed = set(passed) | set(failed)
    classified = completed | set(pending)
    pending.extend(assertion for assertion in scenario.assertions if assertion not in classified)
    artifact_hashes: dict[str, str] = {}
    for outcome in current_scope_outcomes:
        for name, digest in outcome.artifact_hashes.items():
            artifact_hashes[f"{outcome.current_scope.case_id}.{name}"] = digest
    return EvaluationOutcome(
        scenario_id=scenario.id,
        current_scope_outcomes=current_scope_outcomes,
        domain_assertion_outcomes=domain_outcomes,
        artifact_hashes=FrozenMap(artifact_hashes),
        assertions_passed=tuple(passed),
        assertions_failed=tuple(failed),
        assertions_pending=tuple(pending),
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


def _operational_capabilities(
    data_quality_status: DataQualityStatus,
    failure_code: object,
    data_quality: DataQualityResult | None,
) -> tuple[CapabilityState, ...]:
    if data_quality is not None:
        if data_quality.status is not data_quality_status:
            raise ValueError("staged operational quality differs from published run")
        return data_quality.capabilities
    if failure_code == ErrorCode.MISSED_WINDOW.value:
        return tuple(
            CapabilityState(
                capability=capability,
                available=False,
                reason_codes=(ErrorCode.MISSED_WINDOW,),
                evidence_ids=(),
            )
            for capability in Capability
        )
    raise ValueError("operational scenario is missing its frozen quality result")


def _provider_call_count(dependencies: RunDependencies) -> int:
    calls = getattr(dependencies.market_data, "calls", None)
    if not isinstance(calls, (list, tuple)):
        raise TypeError("offline market-data fixtures must expose a call log")
    return len(calls)


def _report_section_bullets(report: str, title: str) -> tuple[str, ...] | None:
    heading = f"### {title}"
    lines = report.splitlines()
    heading_indexes = tuple(index for index, line in enumerate(lines) if line == heading)
    if len(heading_indexes) != 1:
        return None
    start = heading_indexes[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("#")),
        len(lines),
    )
    return tuple(line for line in lines[start:end] if line.startswith("- "))


def _source_limitations_adjacent(packet: ResearchPacket, report: str) -> bool:
    warnings = _report_section_bullets(report, "Data Warnings")
    limitations = _report_section_bullets(report, "Data Quality and Limitations")
    if warnings is None or limitations is None:
        return False
    disabled = tuple(state for state in packet.capability_states if not state.available)
    if not disabled:
        return False
    expected_rows = tuple(
        f"- Disabled capability: {state.capability.value} "
        f"({', '.join(reason.value for reason in state.reason_codes)})"
        for state in disabled
    )
    for bullets in (warnings, limitations):
        actual_rows = tuple(
            line for line in bullets if line.startswith("- Disabled capability: ")
        )
        if actual_rows != expected_rows:
            return False
    plan_state = next(
        state
        for state in packet.capability_states
        if state.capability is Capability.PLAN_DRAFT_AVAILABLE
    )
    return plan_state.available or not packet.deterministic_plan_inputs


def _watchlist_exclusions_visible(packet: ResearchPacket, report: str) -> bool:
    bullets = _report_section_bullets(report, "Blocked and Excluded Candidates")
    if bullets is None:
        return False
    expected_rows = tuple(
        f"- {_inline_text(exclusion.symbol)}: "
        f"{', '.join(_inline_text(reason) for reason in exclusion.reason_codes)}"
        for exclusion in packet.candidate_exclusions
    )
    if not expected_rows:
        return bullets == ("- No deterministic reduced content.",)
    return bullets == expected_rows
