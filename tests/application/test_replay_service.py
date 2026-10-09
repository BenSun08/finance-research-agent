import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from finance_research_agent.application.operational_report import render_operational_report
from finance_research_agent.application.performance_telemetry import RunTelemetryRecorder
from finance_research_agent.application.reduced_report import (
    render_reduced_report,
    render_reduced_report_base,
)
from finance_research_agent.application.replay_service import (
    ArtifactNotFoundError,
    replay_published_artifact,
)
from finance_research_agent.application.report_renderer import render_markdown_report
from finance_research_agent.domain.enums import (
    BriefOrigin,
    DataQualityStatus,
    ExecutionStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    ComponentVersions,
    PublishedArtifact,
    PublishedRunBundle,
)
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import validate_research_brief


class _Reader:
    def __init__(
        self,
        bundle: PublishedRunBundle | None,
        report: str | None,
        artifact: PublishedArtifact | None = None,
    ) -> None:
        self.bundle = bundle
        self.report = report
        self.artifact = artifact or (
            PublishedArtifact(
                run_id=bundle.run.run_id,
                bundle_sha256=sha256(canonical_bytes(bundle)).hexdigest(),
                markdown_sha256=sha256(report.encode()).hexdigest(),
                published_at=datetime(2026, 8, 26, tzinfo=UTC),
            )
            if bundle is not None and report is not None else None
        )
        self.calls: list[str] = []

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None:
        self.calls.append(f"bundle:{run_id}")
        return self.bundle

    def get_report(self, run_id: str) -> str | None:
        self.calls.append(f"report:{run_id}")
        return self.report

    def get_published_artifact(self, run_id: str) -> PublishedArtifact | None:
        self.calls.append(f"artifact:{run_id}")
        return self.artifact


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
    bundle = _frozen_bundle(valid_packet, None, BriefOrigin.DETERMINISTIC_REDUCED)
    return bundle, bundle.report_markdown


def test_replay_returns_the_frozen_bundle_without_collection_or_synthesis(
    valid_packet,
) -> None:
    bundle, report = _published_fixture(valid_packet)
    reader = _Reader(bundle, report)

    result = replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))

    assert result.bundle is bundle
    assert result.report_markdown == report
    assert result.run_id == bundle.run.run_id
    assert result.json_matches and result.markdown_matches
    assert result.stored_json_sha256 == result.replayed_json_sha256
    assert result.stored_markdown_sha256 == result.replayed_markdown_sha256
    assert result.component_version_mismatches == ()
    assert result.telemetry is not None
    assert reader.calls == [
        f"bundle:{bundle.run.run_id}", f"report:{bundle.run.run_id}",
        f"artifact:{bundle.run.run_id}",
    ]


def test_replay_rejects_missing_publication(valid_packet) -> None:
    bundle, _ = _published_fixture(valid_packet)
    reader = _Reader(None, None)

    with pytest.raises(ArtifactNotFoundError):
        replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("core_version", "9.9.9"),
        ("mcp_contract_version", "9.9.9"),
        ("plugin_version", "9.9.9"),
        ("skill_version", "9.9.9"),
        ("prompt_version", "9.9.9"),
        ("report_template_version", "9.9.9"),
        ("schema_versions", FrozenMap({"different": "9.9.9"})),
        ("watchlist_version", "different"),
        ("regime_policy_version", "different"),
        ("setup_policy_version", "different"),
        ("risk_policy_version", "different"),
        ("source_policy_version", "different"),
    ),
)
def test_replay_fails_closed_when_a_component_version_differs(
    valid_packet, field, replacement
) -> None:
    bundle, report = _published_fixture(valid_packet)
    reader = _Reader(bundle, report)
    current_versions = _versions(bundle.run).model_copy(update={field: replacement})

    result = replay_published_artifact(reader, bundle.run.run_id, current_versions)
    assert result.component_version_mismatches == (field,)
    assert result.json_matches
    assert not result.markdown_matches
    assert result.replayed_markdown_sha256 is None


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


def _frozen_bundle(valid_packet, valid_brief_draft, origin: BriefOrigin):
    packet = valid_packet
    run = packet.run.model_copy(update={"execution_status": ExecutionStatus.PUBLISHED})
    if origin is BriefOrigin.SYNTHESIZED:
        validation = validate_research_brief(packet, valid_brief_draft, 1)
        assert validation.is_valid
        report = render_markdown_report(packet, valid_brief_draft, validation)
        contents = {
            "research_packet": packet.model_dump(mode="json"),
            "brief_draft": json.loads(canonical_bytes(valid_brief_draft)),
            "validation_report": json.loads(canonical_bytes(validation)),
        }
    elif origin is BriefOrigin.DETERMINISTIC_REDUCED:
        reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
        report = render_reduced_report(packet, reason)
        contents = {
            "research_packet": packet.model_dump(mode="json"),
            "brief_origin": origin.value,
            "reduced_report_reason": reason.value,
            "reduced_report_staged_sha256": sha256(render_reduced_report_base(packet)).hexdigest(),
            "reduced_plans": [],
            "validation_reports": [],
        }
    else:
        reason_code = ErrorCode.MARKET_CALENDAR_UNAVAILABLE
        run = run.model_copy(update={"data_quality_status": DataQualityStatus.FAIL})
        report = render_operational_report(run, reason_code)
        contents = {"brief_origin": origin.value, "failure_code": reason_code.value}
    telemetry = RunTelemetryRecorder(monotonic_ns=lambda: 0).snapshot()
    contents["performance_telemetry"] = telemetry.model_dump(mode="json")
    contents["performance_telemetry_sha256"] = sha256(canonical_bytes(telemetry)).hexdigest()
    return PublishedRunBundle(
        run=run,
        bundle=FrozenMap(contents),
        report_markdown=report,
        markdown_sha256=sha256(report.encode()).hexdigest(),
    )


def test_replay_verifies_and_returns_frozen_telemetry_without_live_dependencies(
    valid_packet,
) -> None:
    bundle, report = _published_fixture(valid_packet)
    reader = _Reader(bundle, report)

    result = replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))

    assert result.telemetry == RunTelemetryRecorder(monotonic_ns=lambda: 0).snapshot()
    assert reader.calls == [
        f"bundle:{bundle.run.run_id}", f"report:{bundle.run.run_id}",
        f"artifact:{bundle.run.run_id}",
    ]


def test_replay_rejects_telemetry_digest_mismatch(valid_packet) -> None:
    bundle, report = _published_fixture(valid_packet)
    contents = dict(bundle.bundle)
    contents["performance_telemetry_sha256"] = "0" * 64
    changed = bundle.model_copy(update={"bundle": FrozenMap(contents)})

    with pytest.raises(ValueError, match="telemetry.*hash"):
        replay_published_artifact(
            _Reader(changed, report), bundle.run.run_id, _versions(bundle.run)
        )


def test_replay_rejects_malformed_telemetry(valid_packet) -> None:
    bundle, report = _published_fixture(valid_packet)
    contents = dict(bundle.bundle)
    contents["performance_telemetry"] = {"provider_request_counts": {"secret": 1}}
    changed = bundle.model_copy(update={"bundle": FrozenMap(contents)})

    with pytest.raises(ValueError, match="telemetry"):
        replay_published_artifact(
            _Reader(changed, report), bundle.run.run_id, _versions(bundle.run)
        )


@pytest.mark.parametrize("origin", tuple(BriefOrigin))
def test_replay_reconstructs_each_report_origin_from_frozen_inputs(
    valid_packet, valid_brief_draft, origin
) -> None:
    bundle = _frozen_bundle(valid_packet, valid_brief_draft, origin)
    reader = _Reader(bundle, bundle.report_markdown)

    result = replay_published_artifact(reader, bundle.run.run_id, _versions(bundle.run))

    assert result.report_markdown == bundle.report_markdown
    assert reader.calls == [
        f"bundle:{bundle.run.run_id}", f"report:{bundle.run.run_id}",
        f"artifact:{bundle.run.run_id}",
    ]


@pytest.mark.parametrize("origin", tuple(BriefOrigin))
def test_replay_rejects_report_drift_even_when_frozen_hash_matches(
    valid_packet, valid_brief_draft, origin
) -> None:
    bundle = _frozen_bundle(valid_packet, valid_brief_draft, origin)
    changed = bundle.report_markdown.replace("# Premarket", "# Altered Premarket", 1)
    tampered = bundle.model_copy(update={
        "report_markdown": changed,
        "markdown_sha256": sha256(changed.encode()).hexdigest(),
    })

    with pytest.raises(ValueError, match="reconstructed report"):
        replay_published_artifact(
            _Reader(tampered, changed), tampered.run.run_id, _versions(tampered.run)
        )


def test_replay_rejects_unknown_frozen_report_origin(valid_packet) -> None:
    report = "# Frozen brief\n"
    bundle = PublishedRunBundle(
        run=valid_packet.run.model_copy(update={"execution_status": ExecutionStatus.PUBLISHED}),
        bundle=FrozenMap({"packet_id": valid_packet.packet_id}),
        report_markdown=report,
        markdown_sha256=sha256(report.encode()).hexdigest(),
    )

    with pytest.raises(ValueError, match="report origin"):
        replay_published_artifact(_Reader(bundle, report), bundle.run.run_id, _versions(bundle.run))


def test_replay_rejects_bundle_report_text_that_differs_from_indexed_report(
    valid_packet, valid_brief_draft
) -> None:
    bundle = _frozen_bundle(valid_packet, valid_brief_draft, BriefOrigin.OPERATIONAL)
    changed = bundle.model_copy(update={"report_markdown": "# unrelated\n"})

    with pytest.raises(ValueError, match="reconstructed report"):
        replay_published_artifact(
            _Reader(changed, bundle.report_markdown), bundle.run.run_id, _versions(bundle.run)
        )


def test_replay_rejects_reduced_base_hash_drift(valid_packet) -> None:
    bundle, report = _published_fixture(valid_packet)
    changed_contents = dict(bundle.bundle)
    changed_contents["reduced_report_staged_sha256"] = "0" * 64
    changed = bundle.model_copy(update={"bundle": FrozenMap(changed_contents)})

    with pytest.raises(ValueError, match="reduced report base"):
        replay_published_artifact(
            _Reader(changed, report), bundle.run.run_id, _versions(bundle.run)
        )


def test_replay_rejects_packet_identity_drift(valid_packet, valid_brief_draft) -> None:
    bundle = _frozen_bundle(valid_packet, valid_brief_draft, BriefOrigin.SYNTHESIZED)
    changed_run = bundle.run.model_copy(update={"core_version": "changed"})
    changed = bundle.model_copy(update={"run": changed_run})

    with pytest.raises(ValueError, match="packet run differs"):
        replay_published_artifact(
            _Reader(changed, bundle.report_markdown), bundle.run.run_id,
            _versions(changed_run),
        )


def test_replay_rejects_repair_exhaustion_without_three_recorded_failures(
    valid_packet
) -> None:
    bundle, _ = _published_fixture(valid_packet)
    reason = ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED
    report = render_reduced_report(valid_packet, reason)
    contents = dict(bundle.bundle)
    contents["reduced_report_reason"] = reason.value
    changed = bundle.model_copy(update={
        "bundle": FrozenMap(contents),
        "report_markdown": report,
        "markdown_sha256": sha256(report.encode()).hexdigest(),
    })

    with pytest.raises(ValueError, match="validation reports"):
        replay_published_artifact(
            _Reader(changed, report), bundle.run.run_id, _versions(bundle.run)
        )


@pytest.mark.parametrize("tamper", ("wrong_run", "valid_report"))
def test_replay_rejects_unbound_reduced_validation_reports(
    valid_packet, valid_brief_draft, tamper
) -> None:
    bundle, report = _published_fixture(valid_packet)
    if tamper == "wrong_run":
        bad_draft = valid_brief_draft.model_copy(update={"run_id": "premarket-2026-01-01-r2"})
        validation = validate_research_brief(valid_packet, bad_draft, 1).model_copy(
            update={"run_id": "premarket-2026-01-01-r2"}
        )
    else:
        validation = validate_research_brief(valid_packet, valid_brief_draft, 1)
        assert validation.is_valid
    contents = dict(bundle.bundle)
    contents["validation_reports"] = [json.loads(canonical_bytes(validation))]
    changed = bundle.model_copy(update={"bundle": FrozenMap(contents)})

    with pytest.raises(ValueError, match="validation reports"):
        replay_published_artifact(
            _Reader(changed, report), bundle.run.run_id, _versions(bundle.run)
        )


def test_version_drift_is_reported_before_current_renderer_difference(
    valid_packet, valid_brief_draft
) -> None:
    bundle = _frozen_bundle(valid_packet, valid_brief_draft, BriefOrigin.SYNTHESIZED)
    older_report = bundle.report_markdown.replace("# Premarket", "# Former Premarket", 1)
    older_bundle = bundle.model_copy(update={
        "report_markdown": older_report,
        "markdown_sha256": sha256(older_report.encode()).hexdigest(),
    })
    current = _versions(bundle.run).model_copy(update={"report_template_version": "0.2"})

    result = replay_published_artifact(
        _Reader(older_bundle, older_report), bundle.run.run_id, current
    )
    assert result.component_version_mismatches == ("report_template_version",)
    assert result.json_matches and not result.markdown_matches
    assert result.replayed_markdown_sha256 is None


def test_replay_rejects_indexed_json_digest_that_disagrees_with_bundle(
    valid_packet
) -> None:
    bundle, report = _published_fixture(valid_packet)
    receipt = PublishedArtifact(
        run_id=bundle.run.run_id,
        bundle_sha256="0" * 64,
        markdown_sha256=sha256(report.encode()).hexdigest(),
        published_at=datetime(2026, 8, 26, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="bundle JSON"):
        replay_published_artifact(
            _Reader(bundle, report, receipt), bundle.run.run_id, _versions(bundle.run)
        )


def test_installed_skill_version_drift_skips_reconstruction_and_preserves_json_integrity(
    valid_packet, monkeypatch
) -> None:
    from finance_research_agent.application import component_versions, replay_service

    run = valid_packet.run.model_copy(update={
        "skill_version": "sha256:" + "a" * 64, "plugin_version": "0.1.0",
    })
    packet = valid_packet.model_copy(update={"run": run})
    bundle = _frozen_bundle(packet, None, BriefOrigin.OPERATIONAL)
    monkeypatch.setattr(
        component_versions, "load_installed_skill_versions",
        lambda: ("sha256:" + "b" * 64, "0.1.0"),
    )
    renderer_calls = []

    def forbidden_render(_bundle):
        renderer_calls.append("render")
        raise AssertionError("skill drift must stop before reconstruction")

    monkeypatch.setattr(replay_service, "_reconstructed_report", forbidden_render)
    reader = _Reader(bundle, bundle.report_markdown)
    result = replay_published_artifact(
        reader, bundle.run.run_id,
        component_versions.current_component_versions(bundle.run.configuration_snapshot),
    )
    assert result.component_version_mismatches == ("skill_version",)
    assert result.json_matches is True
    assert result.markdown_matches is False
    assert result.replayed_markdown_sha256 is None
    assert result.bundle == bundle
    assert result.report_markdown == bundle.report_markdown
    assert renderer_calls == []
    assert reader.calls == [
        f"bundle:{bundle.run.run_id}", f"report:{bundle.run.run_id}",
        f"artifact:{bundle.run.run_id}",
    ]
