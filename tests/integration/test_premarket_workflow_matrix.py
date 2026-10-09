"""Offline path coverage for the fixed, declarative premarket contract."""

from importlib import import_module
from importlib.util import find_spec

import pytest

from finance_research_agent.domain.enums import ExecutionStatus, ReducedReportReason
from finance_research_agent.domain.types import canonical_bytes


def _support():
    assert find_spec("tests.support.premarket_workflow") is not None, (
        "the finite test-only workflow protocol harness is missing"
    )
    return import_module("tests.support.premarket_workflow")


@pytest.mark.parametrize("repairs", [0, 1, 2])
def test_valid_publication_after_bounded_repairs(
    tmp_path, valid_packet, valid_brief_draft, repairs
):
    support = _support()
    protocol, packet, draft = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    invalid = [
        draft.model_copy(
            update={
                "execution_status": ExecutionStatus.FAILED,
                "data_warnings": (f"attempt-{index}",),
            }
        )
        for index in range(repairs)
    ]
    host = support.ScriptedSynthesis([*invalid, draft])
    before = canonical_bytes(packet)

    result = support.run_workflow(protocol, host)

    assert result.outcome == "published"
    assert result.validations == repairs + 1 <= 3
    assert result.repairs == repairs <= 2
    assert protocol.names == (
        "get_system_status",
        "validate_configuration",
        "prepare_premarket_run",
        *("validate_and_publish_brief",) * (repairs + 1),
        "get_report",
    )
    assert host.packet_objects == [packet] * (repairs + 1)
    assert all(value is host.packet_objects[0] for value in host.packet_objects)
    assert host.packet_hashes == [packet.canonical_sha256] * (repairs + 1)
    assert canonical_bytes(packet) == before
    assert result.report.bundle["brief_draft"]["origin"] == "SYNTHESIZED"
    assert "brief_origin" not in result.report.bundle
    assert result.report.model_dump(mode="json")["bundle"]["research_packet"] == packet.model_dump(
        mode="json"
    )


@pytest.mark.parametrize(
    "failure,reason",
    [
        (None, ReducedReportReason.SYNTHESIS_UNAVAILABLE),
        (TimeoutError(), ReducedReportReason.SYNTHESIS_TIMEOUT),
        ("{not JSON", ReducedReportReason.SYNTHESIS_UNAVAILABLE),
        ('{"invented":"draft"}', ReducedReportReason.SYNTHESIS_UNAVAILABLE),
    ],
)
def test_host_failure_uses_typed_reduced_path(
    tmp_path, valid_packet, valid_brief_draft, failure, reason
):
    support = _support()
    protocol, packet, _ = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)

    result = support.run_workflow(protocol, support.ScriptedSynthesis([failure]))

    assert result.outcome == "deterministic_reduced"
    assert result.validations == 0
    assert protocol.names[-2:] == ("publish_reduced_report", "get_report")
    assert protocol.requests[-2].reason is reason
    assert result.report.bundle["brief_origin"] == "DETERMINISTIC_REDUCED"
    assert result.report.model_dump(mode="json")["bundle"]["research_packet"] == packet.model_dump(
        mode="json"
    )


def test_three_invalid_validations_exhaust_two_repairs(tmp_path, valid_packet, valid_brief_draft):
    support = _support()
    protocol, packet, draft = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    host = support.ScriptedSynthesis(
        [
            draft.model_copy(
                update={
                    "execution_status": ExecutionStatus.FAILED,
                    "data_warnings": (f"attempt-{index}",),
                }
            )
            for index in range(3)
        ]
    )

    result = support.run_workflow(protocol, host)

    assert result.outcome == "deterministic_reduced"
    assert (result.validations, result.repairs) == (3, 2)
    assert protocol.requests[-2].reason is ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED
    assert len(result.report.bundle["validation_reports"]) == 3
    assert host.packet_hashes == [packet.canonical_sha256] * 3
    assert [issues.validation_attempt for issues in host.issues[1:]] == [1, 2]


@pytest.mark.parametrize("gate", ["status", "configuration", "skipped", "prepare"])
def test_failed_preparation_gates_block_synthesis(tmp_path, valid_packet, valid_brief_draft, gate):
    support = _support()
    protocol, _, _ = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft, gate=gate)
    host = support.ScriptedSynthesis([])

    result = support.run_workflow(protocol, host)

    assert result.outcome == "blocked"
    assert host.packet_objects == []
    assert not set(protocol.names) & {
        "validate_and_publish_brief",
        "publish_reduced_report",
        "get_report",
    }


@pytest.mark.parametrize("origin", ["synthesized", "reduced", "operational"])
def test_existing_publication_reads_once_without_synthesis(
    tmp_path, valid_packet, valid_brief_draft, origin
):
    support = _support()
    protocol, _, _ = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    protocol.publish_existing(origin)
    before = support.repository_bytes(tmp_path)
    assert protocol.preparation().outcome == "PUBLISHED"
    assert protocol.preparation().failure_code is None
    host = support.ScriptedSynthesis([])

    result = support.run_workflow(protocol, host)

    assert (
        result.outcome
        == {
            "synthesized": "published",
            "reduced": "deterministic_reduced",
            "operational": "blocked",
        }[origin]
    )
    assert protocol.names == (
        "get_system_status",
        "validate_configuration",
        "prepare_premarket_run",
        "get_report",
    )
    assert host.packet_objects == []
    assert support.repository_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "operation", ["publish_reduced_report", "get_report", "validate_and_publish_brief"]
)
@pytest.mark.parametrize(
    "response", [{"transport_acknowledged": True}, LookupError("missing artifact")]
)
def test_operation_failure_or_invalid_result_blocks(
    tmp_path, valid_packet, valid_brief_draft, operation, response
):
    support = _support()
    protocol, _, draft = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    protocol.overrides[operation] = response
    host = support.ScriptedSynthesis([None if operation == "publish_reduced_report" else draft])

    result = support.run_workflow(protocol, host)

    assert result.outcome == "blocked"
    assert result.report is None


def test_evidence_injection_cannot_discover_operations_or_change_numeric_truth(
    tmp_path,
    packet_with_injection_text,
    valid_brief_draft,
    draft_that_obeys_injection,
):
    support = _support()
    protocol, packet, draft = support.prepared_protocol(
        tmp_path,
        packet_with_injection_text,
        valid_brief_draft,
    )
    bad = draft_that_obeys_injection.model_copy(
        update={
            "execution_status": packet.run.execution_status,
        }
    )
    before = canonical_bytes(packet)
    result = support.run_workflow(protocol, support.ScriptedSynthesis([bad, draft]))

    assert result.outcome == "published"
    assert result.validations == 2
    assert canonical_bytes(packet) == before
    assert "guaranteed upside" not in result.report.report_markdown
    assert "103.00" in result.report.report_markdown
    assert set(protocol.names) <= set(support.workflow_mcp_operations())
    with pytest.raises(ValueError, match="unknown Product A operation"):
        protocol.dispatch("place_order", "{}")
    with pytest.raises(ValueError, match="unknown Product A operation"):
        protocol.dispatch("fetch_new_evidence", "{}")


def test_post_cutoff_evidence_is_rejected_by_real_packet_builder(valid_packet):
    from datetime import timedelta

    from finance_research_agent.application.packet_service import build_research_packet

    late = valid_packet.evidence[0].model_copy(
        update={
            "source": valid_packet.evidence[0].source.model_copy(
                update={
                    "retrieved_at": valid_packet.run.evidence_cutoff_at + timedelta(seconds=1),
                }
            ),
        }
    )
    with pytest.raises(ValueError, match="cutoff"):
        build_research_packet(
            run=valid_packet.run,
            evidence=(late,),
            snapshots={},
            events=(),
            metrics=(),
            gates=(),
            candidates=(),
            exclusions=(),
            plans=(),
            capabilities=(),
            observations=(),
            max_serialized_bytes=250_000,
        )


@pytest.mark.parametrize("failure", [None, TimeoutError(), "{invalid JSON"])
def test_host_repair_failure_keeps_recorded_validation_and_uses_reduced(
    tmp_path,
    valid_packet,
    valid_brief_draft,
    failure,
):
    support = _support()
    protocol, packet, draft = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    invalid = draft.model_copy(update={"execution_status": ExecutionStatus.FAILED})
    host = support.ScriptedSynthesis([invalid, failure])

    result = support.run_workflow(protocol, host)

    assert result.outcome == "deterministic_reduced"
    assert (result.validations, result.repairs) == (1, 1)
    assert len(result.report.bundle["validation_reports"]) == 1
    assert host.packet_hashes == [packet.canonical_sha256] * 2


@pytest.mark.parametrize("field", ["packet_sha256", "packet_id", "validation_attempt"])
def test_validation_handoff_must_match_frozen_packet_and_attempt(
    tmp_path,
    valid_packet,
    valid_brief_draft,
    field,
):
    from finance_research_agent.application.operations import ValidateAndPublishBriefResult
    from finance_research_agent.domain.validation import validate_research_brief

    support = _support()
    protocol, packet, draft = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    invalid = draft.model_copy(update={"execution_status": ExecutionStatus.FAILED})
    validation = validate_research_brief(packet, invalid, validation_attempt=1)
    wrong = {"packet_sha256": "f" * 64, "packet_id": "wrong-packet", "validation_attempt": 2}[field]
    protocol.overrides["validate_and_publish_brief"] = ValidateAndPublishBriefResult(
        validation_report=validation.model_copy(
            update={
                field: wrong,
                "repair_attempts_used": 1 if field == "validation_attempt" else 0,
            }
        ),
    )

    result = support.run_workflow(protocol, support.ScriptedSynthesis([invalid, draft]))

    assert result.outcome == "blocked"
    assert protocol.names[-1] == "validate_and_publish_brief"
    assert result.repairs == 0


@pytest.mark.parametrize("field", ["run", "report_markdown", "markdown_sha256"])
def test_incomplete_or_mismatched_typed_report_blocks(
    tmp_path,
    valid_packet,
    valid_brief_draft,
    field,
):
    support = _support()
    protocol, _, _ = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    protocol.publish_existing("synthesized")
    report = protocol.repository.load_published_bundle(valid_packet.run.run_id)
    wrong = {
        "run": report.run.model_copy(update={"run_id": "premarket-2026-08-26-r2", "revision": 2}),
        "report_markdown": None,
        "markdown_sha256": "f" * 64,
    }[field]
    protocol.overrides["get_report"] = report.model_copy(update={field: wrong})

    result = support.run_workflow(protocol, support.ScriptedSynthesis([]))

    assert result.outcome == "blocked"
    assert result.report is None
