"""Frozen replay of the offline workflow publications and contract drift."""

from importlib import import_module

import pytest

from finance_research_agent.application.replay_service import _recorded_versions
from finance_research_agent.application.skill_bundle import compute_skill_version
from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.types import canonical_bytes


def _support():
    support = import_module("tests.support.premarket_workflow")
    assert hasattr(support, "replay_trace"), "test-only frozen replay trace is missing"
    return support


@pytest.mark.parametrize("path", ["synthesized", "reduced", "exhausted", "operational"])
def test_frozen_workflow_replay_reproduces_hashes_and_read_only_trace(
    tmp_path,
    valid_packet,
    valid_brief_draft,
    path,
):
    support = _support()
    protocol, packet, draft = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    if path == "operational":
        protocol.publish_existing(path)
        report = protocol.repository.load_published_bundle(packet.run.run_id)
    else:
        responses = {
            "synthesized": [draft],
            "reduced": [None],
            "exhausted": [
                draft.model_copy(
                    update={
                        "execution_status": ExecutionStatus.FAILED,
                        "data_warnings": (f"attempt-{index}",),
                    }
                )
                for index in range(3)
            ],
        }[path]
        workflow = support.run_workflow(protocol, support.ScriptedSynthesis(responses))
        assert workflow.outcome != "blocked"
        report = workflow.report
    versions = _recorded_versions(report)
    assert versions.skill_version == support.source_skill_version()
    frozen_json = canonical_bytes(report)
    before = support.repository_bytes(tmp_path)

    first, first_trace = support.replay_trace(protocol.repository, report.run.run_id, versions)
    second, second_trace = support.replay_trace(protocol.repository, report.run.run_id, versions)

    assert first.json_matches and first.markdown_matches
    assert second.json_matches and second.markdown_matches
    assert (
        first_trace
        == second_trace
        == (
            "load_published_bundle",
            "get_report",
            "get_published_artifact",
        )
    )
    assert canonical_bytes(first.bundle) == canonical_bytes(second.bundle) == frozen_json
    assert first.stored_json_sha256 == first.replayed_json_sha256 == second.replayed_json_sha256
    assert (
        first.stored_markdown_sha256
        == first.replayed_markdown_sha256
        == second.replayed_markdown_sha256
    )
    assert support.repository_bytes(tmp_path) == before


@pytest.mark.parametrize("resource", ["SKILL.md", "references/workflow-contract.yaml"])
def test_current_skill_or_manifest_byte_drift_preserves_frozen_json(
    tmp_path,
    valid_packet,
    valid_brief_draft,
    resource,
):
    support = _support()
    protocol, _, draft = support.prepared_protocol(tmp_path, valid_packet, valid_brief_draft)
    workflow = support.run_workflow(protocol, support.ScriptedSynthesis([draft]))
    report = workflow.report
    recorded = _recorded_versions(report)
    files = {
        name: (support.SKILL_ROOT / name).read_bytes()
        for name in ("SKILL.md", "references/workflow-contract.yaml")
    }
    files[resource] += b"\n# Changed current contract bytes\n"
    current_digest = compute_skill_version(files)
    assert current_digest != recorded.skill_version
    frozen = canonical_bytes(report)
    before = support.repository_bytes(tmp_path)

    result, trace = support.replay_trace(
        protocol.repository,
        report.run.run_id,
        recorded.model_copy(update={"skill_version": current_digest}),
    )

    assert result.json_matches is True
    assert result.markdown_matches is False
    assert result.replayed_markdown_sha256 is None
    assert result.component_version_mismatches == ("skill_version",)
    assert result.stored_json_sha256 == result.replayed_json_sha256
    assert canonical_bytes(result.bundle) == frozen
    assert result.bundle.run.skill_version == recorded.skill_version
    assert trace == ("load_published_bundle", "get_report", "get_published_artifact")
    assert support.repository_bytes(tmp_path) == before
