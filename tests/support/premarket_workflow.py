"""Finite TEST-ONLY premarket protocol runner; never a production host or engine.

The manifest is the only source of order and operation names. The known step
handlers exercise typed branch semantics; they cannot discover new operations.
External readiness/preparation and host synthesis are offline substitutions.
Publication and reads use the real application dispatcher and filesystem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import yaml
from pydantic import TypeAdapter

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.operations import (
    OPERATION_CONTRACTS,
    SystemStatusResult,
    validate_operation_request,
)
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.premarket_preparation import PreparedPremarketRunResult
from finance_research_agent.application.preparation_service import stage_research_packet
from finance_research_agent.application.publication_service import publish_operational_report
from finance_research_agent.application.services import ApplicationServices
from finance_research_agent.application.skill_bundle import compute_skill_version
from finance_research_agent.domain.enums import (
    DataQualityStatus,
    ExecutionStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.market_calendar import RunWindowDecision
from finance_research_agent.domain.models import PublishedRunBundle, RunCheckpoint
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.domain.validation import ResearchBriefDraft

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills/premarket-research"
MANIFEST = SKILL_ROOT / "references/workflow-contract.yaml"


def contract():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def workflow_mcp_operations():
    return tuple(step["operation"] for step in contract()["steps"] if step["kind"] == "mcp_tool")


def source_skill_version():
    return compute_skill_version(
        {
            "SKILL.md": (SKILL_ROOT / "SKILL.md").read_bytes(),
            "references/workflow-contract.yaml": MANIFEST.read_bytes(),
        }
    )


def repository_bytes(path):
    return {
        str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()
    }


class ScriptedSynthesis:
    """Bounded host substitute records exactly which frozen packet it received."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.packet_objects = []
        self.packet_hashes = []
        self.issues = []

    def draft(self, packet, issues):
        self.packet_objects.append(packet)
        self.packet_hashes.append(packet.canonical_sha256)
        self.issues.append(issues)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class OfflineProtocol:
    """Typed offline preparation plus real publication/read dispatcher."""

    def __init__(self, repository, packet, draft, at, gate):
        self.repository = repository
        self.packet = packet
        self.draft = draft
        self.at = at
        self.gate = gate
        self.calls = []
        self.requests = []
        self.overrides = {}
        self.services = ApplicationServices(
            clock=SimpleNamespace(now_utc=lambda: self.at),
            calendar=object(),
            configuration_repository=object(),
            market_data=object(),
            run_repository=repository,
            published_artifact_reader=repository,
            watchlist_repository=object(),
            feedback_repository=object(),
        )

    @property
    def names(self):
        return tuple(self.calls)

    def preparation(self):
        stored = self.repository.load(self.packet.run.run_id)
        decision = RunWindowDecision(
            self.packet.run.market_date,
            True,
            self.packet.run.delivery_status,
            True,
            False,
            False,
            False,
            "MANUAL",
        )
        if self.gate == "skipped":
            decision = RunWindowDecision(
                self.packet.run.market_date,
                False,
                None,
                False,
                False,
                False,
                False,
                "NON_TRADING_DAY",
            )
            return PreparedPremarketRunResult(outcome="SKIPPED", window_decision=decision)
        if stored.published:
            # failure_code is intentionally optional: origin and quality own classification.
            return PreparedPremarketRunResult(
                outcome="PUBLISHED",
                window_decision=decision,
                stored_run=stored,
                publication=self.repository.get_published_artifact(stored.run_id),
            )
        return PreparedPremarketRunResult(
            outcome="PACKET_READY",
            window_decision=decision,
            stored_run=stored,
            research_packet=self.packet,
        )

    def dispatch(self, operation, arguments_json):
        request = validate_operation_request(operation, arguments_json)
        self.calls.append(operation)
        self.requests.append(request)
        self.at += timedelta(seconds=1)
        if operation in self.overrides:
            value = self.overrides[operation]
        elif operation == "get_system_status":
            value = SystemStatusResult(
                configuration_ready=self.gate != "status",
                market_data_ready=True,
                market_calendar_ready=True,
                current_market_date=self.packet.run.market_date,
                diagnostics=(),
            )
        elif operation == "validate_configuration":
            if self.gate == "configuration":
                raise ValueError("invalid configuration")
            value = self.packet.run.configuration_snapshot
        elif operation == "prepare_premarket_run":
            if self.gate == "prepare":
                raise RuntimeError("preparation failed")
            value = self.preparation()
        else:
            value = self.services.dispatch(operation, arguments_json)
        if isinstance(value, Exception):
            raise value
        return TypeAdapter(OPERATION_CONTRACTS[operation].result_model).validate_python(
            value,
            strict=True,
        )

    def publish_existing(self, origin):
        run_id = self.packet.run.run_id
        if origin == "synthesized":
            self.services.dispatch(
                "validate_and_publish_brief",
                json.dumps(
                    {
                        "draft": self.draft.model_dump(mode="json"),
                    }
                ),
            )
        elif origin == "reduced":
            self.services.dispatch(
                "publish_reduced_report",
                json.dumps(
                    {
                        "run_id": run_id,
                        "reason": "SYNTHESIS_UNAVAILABLE",
                    }
                ),
            )
        elif origin == "operational":
            failed = self.packet.run.model_copy(
                update={
                    "execution_status": ExecutionStatus.CREATED,
                    "data_quality_status": DataQualityStatus.FAIL,
                    "evidence_cutoff_at": None,
                }
            )
            self.repository = FileSystemRunRepository(self.repository.root / "operational")
            self.repository.create(failed)
            self.repository.checkpoint(
                run_id,
                RunCheckpoint(
                    run_id=run_id,
                    stage="CREATED",
                    execution_status=ExecutionStatus.CREATED,
                    data_quality_status=DataQualityStatus.FAIL,
                    delivery_status=failed.delivery_status,
                    written_at=failed.invoked_at,
                    evidence_cutoff_at=None,
                    artifact_hashes=FrozenMap({}),
                    resumable=True,
                ),
            )
            self.services._run_repository = self.repository
            self.services._published_artifact_reader = self.repository
            publish_operational_report(self.repository, failed, ErrorCode.INTERNAL_ERROR, self.at)
        else:
            raise ValueError("unknown fixture origin")


def prepared_protocol(path, packet, draft, *, gate=None):
    run = packet.run.model_copy(
        update={
            "execution_status": ExecutionStatus.AWAITING_SYNTHESIS,
            "skill_version": source_skill_version(),
        }
    )
    frozen = build_research_packet(
        run=run,
        evidence=packet.evidence,
        snapshots=packet.market,
        events=packet.events,
        metrics=packet.metrics,
        gates=packet.gates,
        candidates=packet.candidates,
        exclusions=packet.candidate_exclusions,
        plans=packet.deterministic_plan_inputs,
        capabilities=packet.capability_states,
        observations=packet.prior_plan_observations,
        max_serialized_bytes=packet.synthesis_constraints.max_serialized_bytes,
    )
    repository = FileSystemRunRepository(path)
    repository.create(run.model_copy(update={"execution_status": ExecutionStatus.ANALYZING}))
    repository.checkpoint(
        run.run_id,
        RunCheckpoint(
            run_id=run.run_id,
            stage="ANALYZING",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=run.data_quality_status,
            delivery_status=run.delivery_status,
            written_at=run.invoked_at,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({}),
            resumable=True,
        ),
    )
    repository.freeze_evidence(run.run_id, run.evidence_cutoff_at)
    at = run.evidence_cutoff_at + timedelta(seconds=1)
    stage_research_packet(repository, frozen, at)
    adapted = draft.model_copy(
        update={
            "execution_status": run.execution_status,
        }
    )
    return OfflineProtocol(repository, frozen, adapted, at, gate), frozen, adapted


@dataclass(frozen=True)
class WorkflowResult:
    outcome: str
    validations: int
    repairs: int
    report: PublishedRunBundle | None


def run_workflow(protocol, host):
    """Interpret only the seven approved steps and their typed branches."""
    manifest = contract()
    validations = 0
    repairs = 0
    prepared = None
    packet = None
    draft = None
    reason = None
    published = False

    def invoke(step, arguments):
        return protocol.dispatch(step["operation"], json.dumps(arguments))

    try:
        for step in manifest["steps"]:
            if step["id"] == "system_status":
                status = invoke(step, {})
                if not all(
                    (
                        status.configuration_ready,
                        status.market_data_ready,
                        status.market_calendar_ready,
                    )
                ):
                    return WorkflowResult("blocked", validations, repairs, None)
            elif step["id"] == "validate_configuration":
                invoke(step, {})
            elif step["id"] == "prepare_run":
                prepared = invoke(step, {})
                if prepared.outcome == "SKIPPED":
                    return WorkflowResult("blocked", validations, repairs, None)
                packet = prepared.research_packet
                published = prepared.outcome == "PUBLISHED"
            elif step["id"] == "synthesize_draft":
                if published:
                    continue
                try:
                    value = host.draft(packet, None)
                    draft = ResearchBriefDraft.model_validate_json(
                        value if isinstance(value, str) else value.model_dump_json(),
                        strict=True,
                    )
                except TimeoutError:
                    reason = ReducedReportReason.SYNTHESIS_TIMEOUT
                except (ValueError, AttributeError, StopIteration):
                    reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
            elif step["id"] == "validate_and_publish":
                if published or reason is not None:
                    continue
                while validations < manifest["repair"]["max_validations"]:
                    result = invoke(step, {"draft": draft.model_dump(mode="json")})
                    validations += 1
                    if result.publication is not None:
                        published = True
                        break
                    validation = result.validation_report
                    if (
                        validation.packet_id != packet.packet_id
                        or validation.packet_sha256 != packet.canonical_sha256
                        or validation.run_id != packet.run.run_id
                        or validation.validation_attempt != validations
                        or validation.is_valid
                    ):
                        return WorkflowResult("blocked", validations, repairs, None)
                    if repairs == manifest["repair"]["max_repairs"]:
                        reason = ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED
                        break
                    repairs += 1
                    try:
                        value = host.draft(packet, result.validation_report)
                        draft = ResearchBriefDraft.model_validate_json(
                            value if isinstance(value, str) else value.model_dump_json(),
                            strict=True,
                        )
                    except TimeoutError:
                        reason = ReducedReportReason.SYNTHESIS_TIMEOUT
                        break
                    except (ValueError, AttributeError, StopIteration):
                        reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
                        break
            elif step["id"] == "publish_reduced":
                if published:
                    continue
                invoke(step, {"run_id": prepared.stored_run.run_id, "reason": reason.value})
            elif step["id"] == "read_report":
                report = invoke(step, {"run_id": prepared.stored_run.run_id})
                contents = report.model_dump(mode="json")["bundle"]
                if (
                    report.run.execution_status is not ExecutionStatus.PUBLISHED
                    or report.run.run_id != prepared.stored_run.run_id
                    or not report.report_markdown
                    or sha256(report.report_markdown.encode("utf-8")).hexdigest()
                    != report.markdown_sha256
                ):
                    return WorkflowResult("blocked", validations, repairs, None)
                origin = contents.get("brief_origin") or contents.get("brief_draft", {}).get(
                    "origin"
                )
                outcome = {
                    "SYNTHESIZED": "published",
                    "DETERMINISTIC_REDUCED": "deterministic_reduced",
                    "OPERATIONAL": "blocked",
                }.get(origin, "blocked")
                return WorkflowResult(outcome, validations, repairs, report)
            else:
                raise ValueError("unsupported test-only workflow step")
    except (ValueError, RuntimeError, LookupError, TypeError):
        return WorkflowResult("blocked", validations, repairs, None)
    return WorkflowResult("blocked", validations, repairs, None)


def replay_trace(repository, run_id, versions):
    """Observe real frozen replay through its three read-only repository ports."""
    from finance_research_agent.application.replay_service import replay_published_artifact

    trace = []

    class Reader:
        def load_published_bundle(self, requested):
            trace.append("load_published_bundle")
            return repository.load_published_bundle(requested)

        def get_report(self, requested):
            trace.append("get_report")
            return repository.get_report(requested)

        def get_published_artifact(self, requested):
            trace.append("get_published_artifact")
            return repository.get_published_artifact(requested)

    return replay_published_artifact(Reader(), run_id, versions), tuple(trace)
