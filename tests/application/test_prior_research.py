"""Frozen prior publication selection and completed-bar observations."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.prompt_source import canonical_prompt_sha256
from finance_research_agent.domain.enums import BriefOrigin, ExecutionStatus, GateStatus, PlanStatus
from finance_research_agent.domain.models import MarketSnapshot, PublishedRunBundle, RunCheckpoint
from finance_research_agent.domain.plans import build_trade_plan
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import ResearchBriefDraft
from tests.unit.test_observations import _bar

pytest_plugins = ("tests.unit.test_trade_plan",)


@pytest.fixture
def prior_packet(inputs, valid_packet):
    plan = build_trade_plan(**inputs)
    reference = plan.entry_zone.upper.evidence_id
    evidence = valid_packet.evidence[0].model_copy(update={
        "evidence_id": reference,
        "source": valid_packet.evidence[0].source.model_copy(update={
            "observed_at": inputs["run"].evidence_cutoff_at,
            "retrieved_at": inputs["run"].evidence_cutoff_at,
        }),
    })
    run = inputs["run"].model_copy(update={"prompt_version": canonical_prompt_sha256()})
    return build_research_packet(
        run=run, evidence=(evidence,), snapshots={}, events=(), metrics=(), gates=(),
        candidates=(), exclusions=(), plans=(plan,), capabilities=(), observations=(),
        max_serialized_bytes=500_000,
    )


def _publication(repo, packet, *, origin=BriefOrigin.SYNTHESIZED, context_updates=None):
    run = packet.run.model_copy(update={
        "execution_status": ExecutionStatus.PUBLISHED, **(context_updates or {}),
    })
    repo.create(run)
    report = "# Prior research\n"
    contents = {"research_packet": packet.model_dump(mode="json")}
    if origin is BriefOrigin.SYNTHESIZED:
        contents["brief_draft"] = ResearchBriefDraft(
            run_id=packet.run.run_id, origin=origin,
            execution_status=packet.run.execution_status,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            executive_sections=(), detailed_sections=(), claims=(), plan_narratives=(),
            disabled_capability_explanations=(), data_warnings=(),
        ).model_dump(mode="json")
    else:
        contents["brief_origin"] = origin.value
    repo.publish_atomically(PublishedRunBundle(
        run=run, bundle=FrozenMap(contents), report_markdown=report,
        markdown_sha256=hashlib.sha256(report.encode()).hexdigest(),
    ))


def _current(repo, packet, **updates):
    run = packet.run.model_copy(update={
        "run_id": "premarket-2026-09-23-r1", "market_date": date(2026, 9, 23),
        "invoked_at": datetime(2026, 9, 23, 12, 45, tzinfo=UTC),
        "evidence_cutoff_at": None,
        **updates,
    })
    repo.create(run)
    repo.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id, stage="CONFIG_FROZEN", execution_status=run.execution_status,
        data_quality_status=run.data_quality_status, delivery_status=run.delivery_status,
        written_at=run.invoked_at, evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}), resumable=True,
    ))
    return run


def _snapshot(packet, bars):
    from tests.application.test_collection_bridge import _instrument

    plan = packet.deterministic_plan_inputs[0]
    instrument = _instrument().model_copy(update={
        "instrument_id": plan.entry_zone.upper.instrument_id, "symbol": plan.symbol,
    })
    return MarketSnapshot(
        instrument=instrument, latest_price=None, completed_daily_bars=tuple(bars),
        current_session_bars=(), source_observations=(packet.evidence[0].source,),
        quality_flags=(),
    )


def test_freeze_selects_verified_prior_packet_and_binds_immutable_artifact(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    selection = freeze_prior_research(current, repo, current.invoked_at)

    assert selection.run_id == current.run_id
    assert selection.prior_run_id == prior_packet.run.run_id
    assert selection.packet == prior_packet
    assert selection.publication_bundle_sha256 == repo.get_published_artifact(
        prior_packet.run.run_id
    ).bundle_sha256
    payload = repo.read_staged_artifact(current.run_id, "prior_research")
    stored = repo.load(current.run_id)
    assert stored.checkpoints[-1].artifact_hashes["prior_research"] == hashlib.sha256(
        payload
    ).hexdigest()
    assert stored.checkpoints[-1].stage == "CONFIG_FROZEN"
    assert stored.evidence_cutoff_at is None


def test_prior_selection_accepts_actual_validated_publication_without_top_level_origin(
    tmp_path, valid_packet, valid_brief_draft, monkeypatch,
):
    from finance_research_agent.application.prior_research import freeze_prior_research
    from finance_research_agent.application.publication_service import (
        publish_validated_brief,
        validate_staged_brief,
    )
    from tests.application.test_publication_service import _prepared

    repo, packet, at = _prepared(tmp_path, valid_packet)
    draft = valid_brief_draft.model_copy(update={
        "execution_status": ExecutionStatus.AWAITING_SYNTHESIS,
    })
    validation, repair = validate_staged_brief(repo, packet, draft, at)
    assert validation.is_valid and repair is None
    published = publish_validated_brief(repo, packet, at + timedelta(seconds=1))
    contents = repo.load_published_bundle(packet.run.run_id).model_dump(mode="json")["bundle"]
    assert "brief_origin" not in contents
    assert contents["brief_draft"]["origin"] == "SYNTHESIZED"
    current = _current(repo, packet)

    selection = freeze_prior_research(current, repo, current.invoked_at)

    assert selection.packet == packet
    assert selection.prior_run_id == published.run_id
    assert selection.publication_bundle_sha256 == published.bundle_sha256
    assert selection.publication_origin is BriefOrigin.SYNTHESIZED
    monkeypatch.setattr(repo, "load_published_bundle", lambda _: pytest.fail("publication reread"))
    assert freeze_prior_research(current, repo, current.invoked_at) == selection


@pytest.mark.parametrize("corruption", [
    "malformed_draft", "wrong_origin", "wrong_run_id", "conflicting_origin",
    "top_level_synthesized", "missing_origin",
])
def test_prior_selection_rejects_invalid_publication_origin(
    tmp_path, prior_packet, corruption,
):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    run_id = prior_packet.run.run_id
    target = tmp_path / "runs/2026/2026-09-22" / run_id / "bundle.json"
    envelope = json.loads(target.read_bytes())
    contents = envelope["bundle"]
    if corruption == "malformed_draft":
        contents["brief_draft"] = {"origin": "SYNTHESIZED"}
    elif corruption == "wrong_origin":
        contents["brief_draft"]["origin"] = "DETERMINISTIC_REDUCED"
    elif corruption == "wrong_run_id":
        contents["brief_draft"]["run_id"] = "premarket-2026-09-21-r1"
    elif corruption == "conflicting_origin":
        contents["brief_origin"] = "DETERMINISTIC_REDUCED"
    else:
        del contents["brief_draft"]
        if corruption == "top_level_synthesized":
            contents["brief_origin"] = "SYNTHESIZED"
    target.write_text(json.dumps(envelope), encoding="utf-8")
    index_path = tmp_path / "reports/2026/2026-09-22/index.json"
    index = json.loads(index_path.read_bytes())
    index[run_id]["bundle_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(ValueError):
        freeze_prior_research(current, repo, current.invoked_at)
    assert repo.read_staged_artifact(current.run_id, "prior_research") is None


def test_prior_selection_accepts_actual_reduced_publication_and_skips_observations(
    tmp_path, valid_packet,
):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
        observe_prior_research,
    )
    from finance_research_agent.application.publication_service import publish_reduced_report
    from finance_research_agent.domain.enums import ReducedReportReason
    from tests.application.test_publication_service import _prepared

    repo, packet, at = _prepared(tmp_path, valid_packet)
    published = publish_reduced_report(
        repo, packet, ReducedReportReason.SYNTHESIS_UNAVAILABLE, at,
    )
    current = _current(repo, packet)
    selection = freeze_prior_research(current, repo, current.invoked_at)
    assert selection.prior_run_id == published.run_id
    assert selection.publication_origin is BriefOrigin.DETERMINISTIC_REDUCED
    observed = observe_prior_research(selection, {}, date(2026, 8, 26))
    assert observed.observations == () and observed.gates == () and observed.evidence == ()


def test_resume_restores_selection_after_cutoff_without_query_or_publication_reads(
    tmp_path, prior_packet, monkeypatch,
):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    first = freeze_prior_research(current, repo, current.invoked_at)
    frozen = repo.freeze_evidence(current.run_id, current.invoked_at + timedelta(minutes=1))

    def forbidden(*args):
        raise AssertionError("resume must not select or refresh published history")

    for name in ("get_previous_research_run", "load_published_bundle", "get_published_artifact"):
        monkeypatch.setattr(repo, name, forbidden)
    before = repo.load(current.run_id)
    assert freeze_prior_research(frozen.run, repo, frozen.evidence_cutoff_at) == first
    assert repo.load(current.run_id) == before


def test_no_prior_publication_is_frozen_and_not_reselected(tmp_path, prior_packet, monkeypatch):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    first = freeze_prior_research(current, repo, current.invoked_at)
    assert first.prior_run_id is None and first.packet is None
    monkeypatch.setattr(repo, "get_previous_research_run", lambda _: pytest.fail("reselected"))
    assert freeze_prior_research(current, repo, current.invoked_at) == first


def test_missing_prior_selection_after_cutoff_fails_closed(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    frozen = repo.freeze_evidence(current.run_id, current.invoked_at)
    with pytest.raises(ValueError, match="selection.*cutoff"):
        freeze_prior_research(frozen.run, repo, frozen.evidence_cutoff_at)


def test_corrupt_selection_hash_is_not_replaced(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    freeze_prior_research(current, repo, current.invoked_at)
    path = (
        tmp_path / "runs/2026/2026-09-23/.staging" / current.run_id / "artifacts/prior_research.bin"
    )
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash"):
        freeze_prior_research(current, repo, current.invoked_at)
    assert path.read_bytes() == b"corrupt"


def test_observations_delegate_completed_bar_semantics_and_preserve_reference_evidence(
    tmp_path, prior_packet,
):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
        observe_prior_research,
    )
    from finance_research_agent.domain.observations import observe_prior_plan

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    selection = freeze_prior_research(current, repo, current.invoked_at)
    plan = prior_packet.deterministic_plan_inputs[0]
    entry = plan.entry_zone.upper.value
    bar = _bar(plan, date(2026, 9, 22), entry - 1, entry + 1, entry)
    result = observe_prior_research(
        selection, {plan.symbol: _snapshot(prior_packet, (bar,))}, date(2026, 9, 22),
    )
    assert result.observations == (observe_prior_plan(plan, (bar,), date(2026, 9, 22)),)
    assert result.gates == ()
    assert result.evidence == prior_packet.evidence


@pytest.mark.parametrize("available", [False, True])
def test_missing_symbol_or_insufficient_completed_history_discloses_blocked_observation(
    tmp_path, prior_packet, available,
):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
        observe_prior_research,
    )

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    selection = freeze_prior_research(current, repo, current.invoked_at)
    plan = prior_packet.deterministic_plan_inputs[0]
    snapshots = {plan.symbol: _snapshot(prior_packet, ())} if available else {}
    result = observe_prior_research(selection, snapshots, date(2026, 9, 22))
    assert result.observations == ()
    assert len(result.gates) == 1
    assert result.gates[0].status is GateStatus.BLOCK
    assert result.gates[0].reason_code == "PRIOR_PLAN_OBSERVATION_UNAVAILABLE"


def test_reduced_publication_never_observes_packet_original_draft_plans(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
        observe_prior_research,
    )

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet, origin=BriefOrigin.DETERMINISTIC_REDUCED)
    current = _current(repo, prior_packet)
    selection = freeze_prior_research(current, repo, current.invoked_at)
    result = observe_prior_research(selection, {}, date(2026, 9, 22))
    assert result.observations == () and result.gates == () and result.evidence == ()


def _rebuild(packet, **updates):
    kwargs = dict(
        run=packet.run, evidence=packet.evidence, snapshots=packet.market, events=packet.events,
        metrics=packet.metrics, gates=packet.gates, candidates=packet.candidates,
        exclusions=packet.candidate_exclusions, plans=packet.deterministic_plan_inputs,
        capabilities=packet.capability_states, observations=packet.prior_plan_observations,
        max_serialized_bytes=500_000,
    )
    kwargs.update(updates)
    return build_research_packet(**kwargs)


@pytest.mark.parametrize("status", [PlanStatus.BLOCKED, PlanStatus.EXPIRED])
def test_blocked_and_expired_plans_are_not_observed(tmp_path, prior_packet, status):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
        observe_prior_research,
    )
    from finance_research_agent.domain.plans import expire_plan

    plan = prior_packet.deterministic_plan_inputs[0]
    expired = expire_plan(plan, now_utc=plan.expires_at)
    if status is PlanStatus.BLOCKED:
        expired = expired.model_copy(update={
            "plan_status": status, "effective_expiry_at": None, "expiry_reasons": (),
        })
    packet = _rebuild(prior_packet, plans=(expired,))
    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, packet)
    current = _current(repo, packet)
    result = observe_prior_research(
        freeze_prior_research(current, repo, current.invoked_at), {}, date(2026, 9, 22),
    )
    assert result.observations == () and result.gates == ()


def test_review_required_plan_is_observed(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
        observe_prior_research,
    )

    plan = prior_packet.deterministic_plan_inputs[0].model_copy(update={
        "plan_status": PlanStatus.REVIEW_REQUIRED,
    })
    packet = _rebuild(prior_packet, plans=(plan,))
    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, packet)
    current = _current(repo, packet)
    entry = plan.entry_zone.upper.value
    bar = _bar(plan, date(2026, 9, 22), entry - 1, entry + 1, entry)
    result = observe_prior_research(
        freeze_prior_research(current, repo, current.invoked_at),
        {plan.symbol: _snapshot(packet, (bar,))}, date(2026, 9, 22),
    )
    assert len(result.observations) == 1 and result.gates == ()


def test_unavailable_reference_evidence_blocks_observation(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
        observe_prior_research,
    )

    packet = _rebuild(prior_packet, evidence=())
    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, packet)
    current = _current(repo, packet)
    plan = packet.deterministic_plan_inputs[0]
    result = observe_prior_research(
        freeze_prior_research(current, repo, current.invoked_at),
        {plan.symbol: _snapshot(prior_packet, ())}, date(2026, 9, 22),
    )
    assert result.observations == () and result.evidence == ()
    assert result.gates[0].reason_code == "PRIOR_PLAN_OBSERVATION_UNAVAILABLE"


@pytest.mark.parametrize("has_prior", [False, True])
def test_staged_selection_recovers_after_checkpoint_crash_without_reselection(
    tmp_path, prior_packet, monkeypatch, has_prior,
):
    from finance_research_agent.application.prior_research import (
        freeze_prior_research,
    )

    repo = FileSystemRunRepository(tmp_path)
    if has_prior:
        _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    checkpoint = repo.checkpoint_if_current

    def crash_before_checkpoint(*args):
        raise OSError("simulated crash after immutable artifact staging")

    monkeypatch.setattr(repo, "checkpoint_if_current", crash_before_checkpoint)
    with pytest.raises(OSError, match="simulated crash"):
        freeze_prior_research(current, repo, current.invoked_at)
    staged = repo.read_staged_artifact(current.run_id, "prior_research")
    assert staged is not None
    monkeypatch.setattr(repo, "checkpoint_if_current", checkpoint)

    def forbidden(*args):
        pytest.fail("recovery must use staged selection without publication rereads")

    for name in ("get_previous_research_run", "load_published_bundle", "get_published_artifact"):
        monkeypatch.setattr(repo, name, forbidden)
    recovered = freeze_prior_research(current, repo, current.invoked_at)
    assert recovered.prior_run_id == (prior_packet.run.run_id if has_prior else None)
    assert canonical_bytes(recovered) == staged
    assert repo.read_staged_artifact(current.run_id, "prior_research") == staged
    assert repo.load(current.run_id).checkpoints[-1].artifact_hashes["prior_research"] == (
        hashlib.sha256(staged).hexdigest()
    )


@pytest.mark.parametrize("collection_state", ["artifact", "checkpoint"])
def test_new_selection_cannot_begin_after_collection_has_started(
    tmp_path, prior_packet, monkeypatch, collection_state,
):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    if collection_state == "artifact":
        repo.stage_artifact(current.run_id, "market_data_collection", b"already collected")
    else:
        repo.checkpoint(current.run_id, RunCheckpoint(
            run_id=current.run_id, stage="EVIDENCE_COLLECTED",
            execution_status=current.execution_status,
            data_quality_status=current.data_quality_status,
            delivery_status=current.delivery_status,
            written_at=current.invoked_at, evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({}), resumable=True,
        ))
    before = repo.load(current.run_id)
    monkeypatch.setattr(repo, "get_previous_research_run", lambda _: None)
    with pytest.raises(ValueError, match="selection.*collection"):
        freeze_prior_research(current, repo, current.invoked_at)
    assert repo.load(current.run_id) == before
    assert repo.read_staged_artifact(current.run_id, "prior_research") is None


@pytest.mark.parametrize("problem", ["corrupt", "noncanonical", "wrong_identity", "wrong_date"])
def test_unbound_selection_recovery_rejects_invalid_staged_bytes(
    tmp_path, prior_packet, monkeypatch, problem,
):
    from finance_research_agent.application.prior_research import (
        PriorResearchSelection,
        freeze_prior_research,
    )

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    value = PriorResearchSelection(
        run_id=current.run_id, prior_run_id=None, publication_bundle_sha256=None,
        publication_origin=None, packet=None,
    )
    if problem == "corrupt":
        payload = b"corrupt"
    elif problem == "noncanonical":
        payload = json.dumps(value.model_dump(mode="json"), indent=2).encode()
    elif problem == "wrong_identity":
        payload = canonical_bytes(value.model_copy(update={"run_id": "premarket-2026-09-24-r1"}))
    else:
        packet = _rebuild(prior_packet, run=prior_packet.run.model_copy(update={
            "run_id": "premarket-2026-09-23-r2", "market_date": current.market_date,
            "revision": 2,
        }))
        value = PriorResearchSelection(
            run_id=current.run_id, prior_run_id=packet.run.run_id,
            publication_bundle_sha256="a" * 64,
            publication_origin=BriefOrigin.SYNTHESIZED, packet=packet,
        )
        payload = canonical_bytes(value)
    repo.stage_artifact(current.run_id, "prior_research", payload)
    before = repo.load(current.run_id)
    monkeypatch.setattr(repo, "get_previous_research_run", lambda _: pytest.fail("reselected"))
    with pytest.raises(ValueError):
        freeze_prior_research(current, repo, current.invoked_at)
    assert repo.load(current.run_id) == before
    assert repo.read_staged_artifact(current.run_id, "prior_research") == payload


@pytest.mark.parametrize("boundary", ["collection", "cutoff"])
def test_unbound_selection_cannot_be_bound_after_collection_or_cutoff(
    tmp_path, prior_packet, boundary,
):
    from finance_research_agent.application.prior_research import (
        PriorResearchSelection,
        freeze_prior_research,
    )

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    value = PriorResearchSelection(
        run_id=current.run_id, prior_run_id=None, publication_bundle_sha256=None,
        publication_origin=None, packet=None,
    )
    repo.stage_artifact(current.run_id, "prior_research", canonical_bytes(value))
    if boundary == "collection":
        repo.stage_artifact(current.run_id, "market_data_collection", b"already collected")
    else:
        current = repo.freeze_evidence(current.run_id, current.invoked_at).run
    before = repo.load(current.run_id)
    with pytest.raises(ValueError):
        freeze_prior_research(current, repo, current.invoked_at)
    assert repo.load(current.run_id) == before


def test_recovered_selection_checkpoint_uses_observed_generation(
    tmp_path, prior_packet, monkeypatch,
):
    from finance_research_agent.application.prior_research import (
        PriorResearchSelection,
        freeze_prior_research,
    )

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    value = PriorResearchSelection(
        run_id=current.run_id, prior_run_id=None, publication_bundle_sha256=None,
        publication_origin=None, packet=None,
    )
    payload = canonical_bytes(value)
    repo.stage_artifact(current.run_id, "prior_research", payload)
    checkpoint_if_current = repo.checkpoint_if_current
    original_checkpoint = repo.load(current.run_id).checkpoints[-1]

    def concurrent_checkpoint(run_id, checkpoint, expected_count):
        repo.checkpoint(run_id, original_checkpoint)
        checkpoint_if_current(run_id, checkpoint, expected_count)

    monkeypatch.setattr(repo, "checkpoint_if_current", concurrent_checkpoint)
    with pytest.raises(ValueError, match="state changed"):
        freeze_prior_research(current, repo, current.invoked_at)
    assert all("prior_research" not in item.artifact_hashes for item in (
        repo.load(current.run_id).checkpoints
    ))
    assert repo.read_staged_artifact(current.run_id, "prior_research") == payload
    monkeypatch.setattr(repo, "checkpoint_if_current", checkpoint_if_current)
    assert freeze_prior_research(current, repo, current.invoked_at) == value


def test_missing_staged_selection_hash_is_not_reselected(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    freeze_prior_research(current, repo, current.invoked_at)
    path = (
        tmp_path / "runs/2026/2026-09-23/.staging" / current.run_id / "artifacts/prior_research.bin"
    )
    path.unlink()
    with pytest.raises(ValueError, match="hash"):
        freeze_prior_research(current, repo, current.invoked_at)


@pytest.mark.parametrize("state", ["absent", "published", "different_context"])
def test_selection_requires_current_unpublished_identity(tmp_path, prior_packet, state):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    if state == "absent":
        current = current.model_copy(update={"run_id": "premarket-2026-09-23-r2", "revision": 2})
    elif state == "published":
        _publication(repo, prior_packet)
        current = repo.load(prior_packet.run.run_id).run
    else:
        current = current.model_copy(update={"skill_version": "changed"})
    with pytest.raises(ValueError, match="current unpublished"):
        freeze_prior_research(current, repo, current.invoked_at)


def test_selection_checkpoint_cannot_precede_current_run(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    current = _current(repo, prior_packet)
    with pytest.raises(ValueError, match="time precedes"):
        freeze_prior_research(current, repo, current.invoked_at - timedelta(seconds=1))
    assert repo.read_staged_artifact(current.run_id, "prior_research") is None


@pytest.mark.parametrize("field,value", [
    ("run_id", "premarket-2026-09-24-r1"),
    ("prior_run_id", "premarket-2026-09-21-r1"),
    ("packet", None),
])
def test_restored_selection_checks_hash_bound_reference_identity(
    tmp_path, prior_packet, field, value,
):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    selection = freeze_prior_research(current, repo, current.invoked_at)
    data = selection.model_dump(mode="json")
    data[field] = value
    payload = json.dumps(data).encode()
    staging = tmp_path / "runs/2026/2026-09-23/.staging" / current.run_id
    (staging / "artifacts/prior_research.bin").write_bytes(payload)
    checkpoint = sorted((staging / "checkpoints").glob("*.json"))[-1]
    data = json.loads(checkpoint.read_bytes())
    data["artifact_hashes"]["prior_research"] = hashlib.sha256(payload).hexdigest()
    checkpoint.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        freeze_prior_research(current, repo, current.invoked_at)


def test_prior_cutoff_cannot_follow_current_invocation(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet, invoked_at=(
        prior_packet.run.evidence_cutoff_at - timedelta(seconds=1)
    ))
    with pytest.raises(ValueError, match="strictly earlier"):
        freeze_prior_research(current, repo, current.invoked_at)
    assert repo.read_staged_artifact(current.run_id, "prior_research") is None


def test_prior_packet_must_match_immutable_publication_context(tmp_path, prior_packet):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet, context_updates={"skill_version": "different"})
    current = _current(repo, prior_packet)
    with pytest.raises(ValueError, match="context differs"):
        freeze_prior_research(current, repo, current.invoked_at)


def test_publication_disappearing_after_selection_fails_closed(tmp_path, prior_packet, monkeypatch):
    from finance_research_agent.application.prior_research import freeze_prior_research

    repo = FileSystemRunRepository(tmp_path)
    _publication(repo, prior_packet)
    current = _current(repo, prior_packet)
    query = repo.get_previous_research_run

    def selected_then_removed(market_date):
        selected = query(market_date)
        target = tmp_path / "runs/2026/2026-09-22" / selected / "bundle.json"
        target.unlink()
        return selected

    monkeypatch.setattr(repo, "get_previous_research_run", selected_then_removed)
    with pytest.raises(ValueError, match="missing or corrupt"):
        freeze_prior_research(current, repo, current.invoked_at)
