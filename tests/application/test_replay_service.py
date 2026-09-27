from hashlib import sha256
from importlib import import_module

import pytest

from finance_research_agent.domain.models import ComponentVersions, PublishedRunBundle
from finance_research_agent.domain.types import FrozenMap

try:
    replay_published_artifact = import_module(
        "finance_research_agent.application.replay_service"
    ).replay_published_artifact
    ArtifactNotFoundError = import_module(
        "finance_research_agent.application.replay_service"
    ).ArtifactNotFoundError
    ArtifactVersionMismatchError = import_module(
        "finance_research_agent.application.replay_service"
    ).ArtifactVersionMismatchError
except (ImportError, AttributeError):

    def replay_published_artifact(*args, **kwargs):
        raise AssertionError("frozen-artifact replay is not implemented")

    class ArtifactNotFoundError(RuntimeError):
        pass

    class ArtifactVersionMismatchError(RuntimeError):
        pass


class _Reader:
    def __init__(self, bundle: PublishedRunBundle | None, report: str | None) -> None:
        self.bundle = bundle
        self.report = report
        self.calls: list[str] = []

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None:
        self.calls.append(f"bundle:{run_id}")
        return self.bundle

    def get_report(self, run_id: str) -> str | None:
        self.calls.append(f"report:{run_id}")
        return self.report


def _versions(run) -> ComponentVersions:
    config = run.configuration_snapshot
    return ComponentVersions(
        core_version=run.core_version,
        mcp_contract_version=run.mcp_contract_version,
        plugin_version=run.plugin_version,
        skill_version=run.skill_version,
        prompt_version=run.prompt_version,
        report_template_version=run.report_template_version,
        schema_versions=run.schema_versions,
        watchlist_version=config.watchlist_version,
        regime_policy_version=config.regime_policy_version,
        setup_policy_version=config.setup_policy_version,
        risk_policy_version=config.risk_policy_version,
        source_policy_version=config.source_policy_version,
    )


def _published_fixture(valid_packet):
    report = "# Frozen brief\n"
    bundle = PublishedRunBundle(
        run=valid_packet.run,
        bundle=FrozenMap({"packet_id": valid_packet.packet_id}),
        report_markdown=report,
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
    )
    return bundle, report


def test_replay_returns_the_frozen_bundle_without_collection_or_synthesis(
    valid_packet,
) -> None:
    bundle, report = _published_fixture(valid_packet)
    reader = _Reader(bundle, report)

    result = replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))

    assert result.bundle is bundle
    assert result.report_markdown == report
    assert result.run_id == bundle.run.run_id
    assert reader.calls == [f"bundle:{bundle.run.run_id}", f"report:{bundle.run.run_id}"]


def test_replay_rejects_missing_publication(valid_packet) -> None:
    bundle, _ = _published_fixture(valid_packet)
    reader = _Reader(None, None)

    with pytest.raises(ArtifactNotFoundError):
        replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))


def test_replay_fails_closed_when_a_component_version_differs(valid_packet) -> None:
    bundle, report = _published_fixture(valid_packet)
    reader = _Reader(bundle, report)
    current_versions = _versions(bundle.run).model_copy(
        update={"core_version": "newer-core"}
    )

    with pytest.raises(ArtifactVersionMismatchError, match="core_version"):
        replay_published_artifact(reader, bundle.run.run_id, current_versions)


def test_replay_rejects_report_bytes_that_disagree_with_the_frozen_bundle(
    valid_packet,
) -> None:
    bundle, _ = _published_fixture(valid_packet)
    reader = _Reader(bundle, "# changed brief\n")

    with pytest.raises(ValueError, match="report bytes do not match the frozen bundle"):
        replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))


def test_replay_rejects_a_bundle_stored_under_another_run_id(valid_packet) -> None:
    bundle, report = _published_fixture(valid_packet)
    other_run = bundle.run.model_copy(
        update={"run_id": "premarket-2026-08-27-r1", "market_date": bundle.run.market_date}
    )
    other_bundle = bundle.model_copy(update={"run": other_run})
    reader = _Reader(other_bundle, report)

    with pytest.raises(ValueError, match="run ID does not match"):
        replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))
