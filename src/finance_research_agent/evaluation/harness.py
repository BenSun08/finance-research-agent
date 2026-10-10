"""Finite current-scope evaluation harness over production application services."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Protocol, cast

from finance_research_agent.application.premarket_preparation import (
    PreparedPremarketRunResult,
    prepare_research_packet,
)
from finance_research_agent.application.publication_service import (
    PublicationRepository,
    publish_reduced_report,
    publish_validated_brief,
    validate_staged_brief,
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
    BriefOrigin,
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    InvocationType,
    ReducedReportReason,
    ReportSection,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import CapabilityState
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.quality import DataQualityResult
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import ResearchBriefDraft, ValidationReport
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
    synthesis_factory: (
        Callable[[EvaluationScenario, ScenarioOutcomeExpectation], SynthesisHost] | None
    ) = None

    def __post_init__(self) -> None:
        if type(self.market_date) is not date:
            raise TypeError("market_date must be a date")


class SynthesisHost(Protocol):
    """Offline typed draft source with no provider or tool capability."""

    def draft(
        self, packet: ResearchPacket, issues: ValidationReport | None
    ) -> ResearchBriefDraft: ...


@dataclass(frozen=True, slots=True)
class _SynthesisTrace:
    packet_hashes: tuple[str, ...]
    validation_reports: tuple[ValidationReport, ...]
    invalid_draft_hashes: tuple[str, ...]
    provider_calls_before: int
    error_codes: tuple[ErrorCode, ...]


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
    if prepared.outcome == "SKIPPED":
        if prepared.window_decision.delivery_status is None:
            raise ValueError("skipped scenario is missing its delivery status")
        return _observe_skipped_scenario(
            scenario,
            selected_expectation,
            dependencies,
            repository,
            harness.market_date,
            prepared.window_decision.delivery_status,
        )
    packet: ResearchPacket | None
    synthesis_trace: _SynthesisTrace | None = None
    if prepared.outcome == "PACKET_READY":
        packet = _require_packet(prepared)
        if harness.synthesis_factory is None:
            publish_reduced_report(
                repository,
                packet,
                harness.reduced_report_reason,
                dependencies.clock.now_utc(),
            )
        else:
            synthesis_trace = _publish_with_offline_synthesis(
                scenario,
                selected_expectation,
                harness,
                dependencies,
                repository,
                packet,
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
        synthesis_trace,
    )


def _publish_with_offline_synthesis(
    scenario: EvaluationScenario,
    expectation: ScenarioOutcomeExpectation,
    harness: EvaluationHarness,
    dependencies: RunDependencies,
    repository: PublicationRepository,
    packet: ResearchPacket,
) -> _SynthesisTrace:
    """Run at most one initial draft and two repairs through real app services."""
    assert harness.synthesis_factory is not None
    provider_calls_before = _provider_call_count(dependencies)
    host = harness.synthesis_factory(scenario, expectation)
    draft_method = getattr(host, "draft", None)
    if not callable(draft_method):
        raise TypeError("synthesis_factory must return an offline draft host")
    packet_bytes = canonical_bytes(packet)
    packet_hashes: list[str] = []
    validation_reports: list[ValidationReport] = []
    invalid_draft_hashes: list[str] = []
    error_codes: list[ErrorCode] = []
    previous_report: ValidationReport | None = None
    reduced_reason: ReducedReportReason | None = None
    published = False

    for _ in range(3):
        packet_hashes.append(packet.canonical_sha256)
        candidate: object | None = None
        host_failure: ReducedReportReason | None = None
        try:
            candidate = draft_method(packet, previous_report)
        except TimeoutError:
            host_failure = ReducedReportReason.SYNTHESIS_TIMEOUT
        except Exception:
            host_failure = ReducedReportReason.SYNTHESIS_UNAVAILABLE
        if canonical_bytes(packet) != packet_bytes:
            raise ValueError("offline synthesis changed the frozen research packet")
        if host_failure is not None:
            reduced_reason = host_failure
            break
        try:
            draft = ResearchBriefDraft.model_validate(candidate, strict=True)
        except (TypeError, ValueError):
            reduced_reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
            error_codes.append(ErrorCode.INVALID_RESPONSE)
            break
        if draft.origin is not BriefOrigin.SYNTHESIZED:
            reduced_reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
            error_codes.append(ErrorCode.INVALID_RESPONSE)
            break
        report, repair = validate_staged_brief(
            repository,
            packet,
            draft,
            dependencies.clock.now_utc(),
        )
        if validation_reports and report == validation_reports[-1]:
            reduced_reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
            break
        validation_reports.append(report)
        if not report.is_valid:
            invalid_draft_hashes.append(sha256(canonical_bytes(draft)).hexdigest())
        if report.is_valid:
            publish_validated_brief(repository, packet, dependencies.clock.now_utc())
            published = True
            break
        if repair is None:
            reduced_reason = ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED
            break
        previous_report = report

    if not published:
        if reduced_reason is None:
            reduced_reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
        publish_reduced_report(
            repository,
            packet,
            reduced_reason,
            dependencies.clock.now_utc(),
        )
    return _SynthesisTrace(
        packet_hashes=tuple(packet_hashes),
        validation_reports=tuple(validation_reports),
        invalid_draft_hashes=tuple(invalid_draft_hashes),
        provider_calls_before=provider_calls_before,
        error_codes=tuple(dict.fromkeys(error_codes)),
    )


def _observe_skipped_scenario(
    scenario: EvaluationScenario,
    selected_expectation: ScenarioOutcomeExpectation,
    dependencies: RunDependencies,
    repository: PublicationRepository,
    market_date: date,
    delivery_status: DeliveryStatus,
) -> CurrentScopeServiceObservation:
    record = repository.get_missed_run(market_date)
    observed = ScenarioOutcomeExpectation(
        case_id=selected_expectation.case_id,
        execution_status=ExecutionStatus.SKIPPED,
        data_quality_status=None,
        delivery_status=delivery_status,
        capabilities=(),
        plan_states=(),
        report_banner=None,
        error_codes=(),
        recoverability=None,
        reduced_report_reason=None,
    )
    return CurrentScopeServiceObservation(
        scenario_id=scenario.id,
        current_scope=observed,
        artifact_hashes=FrozenMap({}),
        replay_json_matches=None,
        replay_markdown_matches=None,
        source_limitations_adjacent=None,
        watchlist_exclusions_visible=None,
        provider_call_count=_provider_call_count(dependencies),
        missed_run_record_durable=record is not None,
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
    synthesis_trace: _SynthesisTrace | None = None,
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
    if synthesis_trace is not None:
        serialized_bundle = bundle.model_dump(mode="json")["bundle"]
        _verify_synthesis_trace(repository, run_id, serialized_bundle, synthesis_trace)
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
    if synthesis_trace is not None:
        error_codes = tuple(dict.fromkeys((*error_codes, *synthesis_trace.error_codes)))
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
        reduced_report_reason=(
            ReducedReportReason(reason)
            if isinstance(reason := bundle.bundle.get("reduced_report_reason"), str)
            else None
        ),
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
    final_draft = bundle.model_dump(mode="json")["bundle"].get("brief_draft")
    final_draft_hash = (
        sha256(_canonical_json_bytes(final_draft)).hexdigest()
        if final_draft is not None
        else None
    )
    invalid_draft_never_published = (
        all(digest != final_draft_hash for digest in synthesis_trace.invalid_draft_hashes)
        if synthesis_trace is not None
        else None
    )
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
        synthesis_packet_hashes=(
            synthesis_trace.packet_hashes if synthesis_trace is not None else ()
        ),
        validation_codes=(
            tuple(
                dict.fromkeys(
                    issue.code
                    for report in synthesis_trace.validation_reports
                    for issue in report.issues
                )
            )
            if synthesis_trace is not None
            else ()
        ),
        validation_attempt_count=(
            len(synthesis_trace.validation_reports) if synthesis_trace is not None else 0
        ),
        repair_count=(
            max(0, len(synthesis_trace.packet_hashes) - 1)
            if synthesis_trace is not None
            else 0
        ),
        invalid_draft_never_published=invalid_draft_never_published,
        provider_calls_before_synthesis=(
            synthesis_trace.provider_calls_before if synthesis_trace is not None else None
        ),
    )


def _verify_synthesis_trace(
    repository: PublicationRepository,
    run_id: str,
    bundle: Mapping[str, object],
    trace: _SynthesisTrace,
) -> None:
    stored = repository.load(run_id)
    if stored is None:
        raise ValueError("synthesis publication is missing its stored run")
    validating = tuple(
        checkpoint for checkpoint in stored.checkpoints if checkpoint.stage == "VALIDATING"
    )
    if len(validating) != len(trace.validation_reports):
        raise ValueError("synthesis trace differs from persisted validation attempts")
    expected = tuple(report.model_dump(mode="json") for report in trace.validation_reports)
    recorded_many = bundle.get("validation_reports")
    if recorded_many is not None:
        if _canonical_json_bytes(recorded_many) != _canonical_json_bytes(expected):
            raise ValueError("reduced publication differs from the recorded validation trace")
    elif trace.validation_reports:
        recorded_final = bundle.get("validation_report")
        if _canonical_json_bytes(recorded_final) != _canonical_json_bytes(expected[-1]):
            raise ValueError("synthesized publication differs from the final validation trace")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


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
    if ScenarioAssertionId.PACKET_HASH_STABLE in declared:
        matches = all(
            bool(outcome.synthesis_packet_hashes)
            and len(outcome.synthesis_packet_hashes) <= 3
            and len(set(outcome.synthesis_packet_hashes)) == 1
            and outcome.synthesis_packet_hashes[0]
            == outcome.artifact_hashes.get("research_packet")
            for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(ScenarioAssertionId.PACKET_HASH_STABLE)
    if ScenarioAssertionId.INVALID_DRAFT_NEVER_PUBLISHED in declared:
        matches = all(
            outcome.invalid_draft_never_published is True
            for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(
            ScenarioAssertionId.INVALID_DRAFT_NEVER_PUBLISHED
        )
    if ScenarioAssertionId.REPAIR_LIMIT_ENFORCED in declared:
        matches = all(
            outcome.validation_attempt_count <= 3
            and outcome.repair_count <= 2
            and len(outcome.synthesis_packet_hashes) <= 3
            and outcome.validation_attempt_count <= len(outcome.synthesis_packet_hashes)
            and (
                outcome.current_scope.reduced_report_reason
                is not ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED
                or outcome.validation_attempt_count == 3
            )
            for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(ScenarioAssertionId.REPAIR_LIMIT_ENFORCED)
    if ScenarioAssertionId.UNTRUSTED_EXCERPT_INERT in declared:
        matches = all(
            bool(outcome.synthesis_packet_hashes)
            and len(set(outcome.synthesis_packet_hashes)) == 1
            and outcome.synthesis_packet_hashes[0]
            == outcome.artifact_hashes.get("research_packet")
            and outcome.provider_calls_before_synthesis == outcome.provider_call_count
            and outcome.invalid_draft_never_published is True
            for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(ScenarioAssertionId.UNTRUSTED_EXCERPT_INERT)
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
    if ScenarioAssertionId.NO_PROVIDER_READ_AFTER_WINDOW in declared:
        matches = all(
            outcome.current_scope.delivery_status is DeliveryStatus.MISSED_WINDOW
            and outcome.provider_call_count == 0
            for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(
            ScenarioAssertionId.NO_PROVIDER_READ_AFTER_WINDOW
        )
    if ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE in declared:
        has_skipped_outcome = any(
            outcome.current_scope.execution_status is ExecutionStatus.SKIPPED
            for outcome in current_scope_outcomes
        )
        matches = has_skipped_outcome and all(
            outcome.missed_run_record_durable
            == (outcome.current_scope.execution_status is ExecutionStatus.SKIPPED)
            for outcome in current_scope_outcomes
        )
        (passed if matches else failed).append(
            ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE
        )
    completed = set(passed) | set(failed)
    classified = completed | set(pending)
    pending.extend(assertion for assertion in scenario.assertions if assertion not in classified)
    artifact_hashes: dict[str, str] = {}
    for outcome in current_scope_outcomes:
        for name, digest in outcome.artifact_hashes.items():
            artifact_hashes[f"{outcome.current_scope.case_id}.{name}"] = digest
    passed_in_manifest_order = tuple(item for item in scenario.assertions if item in passed)
    failed_in_manifest_order = tuple(item for item in scenario.assertions if item in failed)
    pending_in_manifest_order = tuple(item for item in scenario.assertions if item in pending)
    return EvaluationOutcome(
        scenario_id=scenario.id,
        current_scope_outcomes=current_scope_outcomes,
        domain_assertion_outcomes=domain_outcomes,
        artifact_hashes=FrozenMap(artifact_hashes),
        assertions_passed=passed_in_manifest_order,
        assertions_failed=failed_in_manifest_order,
        assertions_pending=pending_in_manifest_order,
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
    groups = _report_section_bullet_groups(report, title)
    if groups is None or len(groups) != 1:
        return None
    return groups[0]


def _report_section_bullet_groups(
    report: str, title: str
) -> tuple[tuple[str, ...], ...] | None:
    heading = f"### {title}"
    lines = report.splitlines()
    heading_indexes = tuple(index for index, line in enumerate(lines) if line == heading)
    if not heading_indexes:
        return None
    groups: list[tuple[str, ...]] = []
    for heading_index in heading_indexes:
        start = heading_index + 1
        end = next(
            (index for index in range(start, len(lines)) if lines[index].startswith("#")),
            len(lines),
        )
        groups.append(tuple(line for line in lines[start:end] if line.startswith("- ")))
    return tuple(groups)


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
        if len(actual_rows) != len(expected_rows) or set(actual_rows) != set(expected_rows):
            return False
    affected_sections: Mapping[Capability, tuple[ReportSection, ...]] = {
        Capability.MARKET_SUMMARY_AVAILABLE: (
            ReportSection.MARKET_POSTURE,
            ReportSection.CORE_MARKET_RISKS,
        ),
        Capability.REGIME_CLASSIFICATION_AVAILABLE: (
            ReportSection.MARKET_POSTURE,
            ReportSection.MARKET_REGIME,
        ),
        Capability.WATCHLIST_METRICS_AVAILABLE: (
            ReportSection.WATCHLIST_PRIORITIES,
            ReportSection.WATCHLIST_DASHBOARD,
        ),
        Capability.EVENT_RISK_CHECK_AVAILABLE: (
            ReportSection.TODAY_EVENT_CLOCK,
            ReportSection.MACRO_EVENT_CALENDAR,
        ),
        Capability.SETUP_DETECTION_AVAILABLE: (
            ReportSection.WATCHLIST_PRIORITIES,
            ReportSection.ELIGIBLE_SETUPS,
        ),
        Capability.PLAN_DRAFT_AVAILABLE: (
            ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
            ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
        ),
        Capability.POSITION_SIZING_AVAILABLE: (
            ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
            ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
        ),
        Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE: (
            ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
            ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
        ),
    }
    for state in disabled:
        expected_line = (
            f"- Limitation: {state.capability.value} unavailable "
            f"({', '.join(reason.value for reason in state.reason_codes)})."
        )
        for section in affected_sections[state.capability]:
            section_bullets = _report_section_bullet_groups(report, section.value)
            if section_bullets is None or any(
                expected_line not in bullets for bullets in section_bullets
            ):
                return False
    return True


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
