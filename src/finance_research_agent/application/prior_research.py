"""Freeze prior research identity and observe its conditional plans offline."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from hashlib import sha256
from typing import Protocol, Self

from pydantic import model_validator

from finance_research_agent.application.ports import PublishedArtifactReader, RunRepository
from finance_research_agent.domain.enums import BriefOrigin, Capability, GateStatus, PlanStatus
from finance_research_agent.domain.models import (
    EvidenceItem,
    GateResult,
    Identifier,
    MarketSnapshot,
    RunCheckpoint,
    RunContext,
    Sha256,
    StrictModel,
)
from finance_research_agent.domain.observations import PlanObservation, observe_prior_plan
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import ResearchBriefDraft

_ARTIFACT = "prior_research"


class PriorResearchRepository(RunRepository, PublishedArtifactReader, Protocol):
    """Trusted run storage and indexed immutable publication reads."""


class PriorResearchSelection(StrictModel):
    """A current run's immutable prior publication snapshot, including absence."""

    run_id: Identifier
    prior_run_id: Identifier | None
    publication_bundle_sha256: Sha256 | None
    publication_origin: BriefOrigin | None
    packet: ResearchPacket | None

    @model_validator(mode="after")
    def _coherent_reference(self) -> Self:
        if self.packet is None:
            if any(value is not None for value in (
                self.prior_run_id, self.publication_bundle_sha256, self.publication_origin,
            )):
                raise ValueError("absent prior packet requires an absent publication reference")
        elif (
            self.prior_run_id != self.packet.run.run_id
            or self.publication_bundle_sha256 is None
            or self.publication_origin not in {
                BriefOrigin.SYNTHESIZED, BriefOrigin.DETERMINISTIC_REDUCED,
            }
        ):
            raise ValueError("prior packet requires its matching research publication reference")
        return self


class PriorResearchObservations(StrictModel):
    """Prior market-path observations or explicit unavailability disclosures."""

    observations: tuple[PlanObservation, ...]
    gates: tuple[GateResult, ...]
    evidence: tuple[EvidenceItem, ...]


def _validate_selection(selection: PriorResearchSelection, run: RunContext) -> None:
    if selection.run_id != run.run_id:
        raise ValueError("prior selection belongs to a different current run")
    packet = selection.packet
    if packet is not None and (
        packet.run.market_date >= run.market_date
        or packet.run.require_evidence_cutoff() > run.invoked_at
        or any(plan.run_id != packet.run.run_id for plan in packet.deterministic_plan_inputs)
    ):
        raise ValueError("prior selection requires a strictly earlier frozen research run")


def freeze_prior_research(
    run: RunContext,
    repository: PriorResearchRepository,
    checkpointed_at: datetime,
) -> PriorResearchSelection:
    """Select once before collection, or restore hash-bound bytes without rereads."""
    stored = repository.load(run.run_id)
    if stored is None or stored.published or stored.run != run:
        raise ValueError("prior selection requires the current unpublished run")
    payload = repository.read_staged_artifact(run.run_id, _ARTIFACT)
    hashes = {
        item.artifact_hashes[_ARTIFACT]
        for item in stored.checkpoints if _ARTIFACT in item.artifact_hashes
    }
    if hashes:
        if payload is None or hashes != {sha256(payload).hexdigest()}:
            raise ValueError("prior selection does not match its checkpoint hash")
        selection = PriorResearchSelection.model_validate_json(payload)
        _validate_selection(selection, run)
        return selection
    if stored.evidence_cutoff_at is not None:
        raise ValueError("prior selection cannot be created after evidence cutoff")
    if (
        any(item.stage == "EVIDENCE_COLLECTED" for item in stored.checkpoints)
        or repository.read_staged_artifact(run.run_id, "market_data_collection") is not None
    ):
        raise ValueError("prior selection cannot be created after collection")
    if payload is not None:
        selection = PriorResearchSelection.model_validate_json(payload)
        if canonical_bytes(selection) != payload:
            raise ValueError("unbound prior selection bytes must be canonical")
    else:
        prior_run_id = repository.get_previous_research_run(run.market_date)
        selection = PriorResearchSelection(
            run_id=run.run_id, prior_run_id=None, publication_bundle_sha256=None,
            publication_origin=None, packet=None,
        )
        if prior_run_id is not None:
            bundle = repository.load_published_bundle(prior_run_id)
            receipt = repository.get_published_artifact(prior_run_id)
            if bundle is None or receipt is None or receipt.run_id != prior_run_id:
                raise ValueError("prior publication is missing or corrupt")
            contents = bundle.model_dump(mode="json")["bundle"]
            packet = ResearchPacket.model_validate_json(json.dumps(contents.get("research_packet")))
            if packet.run.model_copy(update={
                "execution_status": bundle.run.execution_status,
                "data_quality_status": bundle.run.data_quality_status,
                "delivery_status": bundle.run.delivery_status,
            }) != bundle.run:
                raise ValueError("prior packet context differs from its publication")
            origin: BriefOrigin
            if "brief_draft" in contents:
                draft = ResearchBriefDraft.model_validate_json(json.dumps(contents["brief_draft"]))
                if (
                    draft.origin is not BriefOrigin.SYNTHESIZED
                    or draft.run_id != packet.run.run_id
                    or "brief_origin" in contents
                ):
                    raise ValueError("prior synthesized publication origin is invalid")
                origin = draft.origin
            else:
                origin = BriefOrigin(contents.get("brief_origin"))
                if origin is not BriefOrigin.DETERMINISTIC_REDUCED:
                    raise ValueError("prior packet publication origin is invalid")
            selection = PriorResearchSelection(
                run_id=run.run_id, prior_run_id=prior_run_id,
                publication_bundle_sha256=receipt.bundle_sha256,
                publication_origin=origin, packet=packet,
            )
    _validate_selection(selection, run)
    if checkpointed_at < run.invoked_at or (
        stored.checkpoints and checkpointed_at < stored.checkpoints[-1].written_at
    ):
        raise ValueError("prior selection checkpoint time precedes current run state")
    latest = stored.checkpoints[-1] if stored.checkpoints else None
    checkpoint = RunCheckpoint(
        run_id=run.run_id, stage="CONFIG_FROZEN",
        execution_status=latest.execution_status if latest else run.execution_status,
        data_quality_status=latest.data_quality_status if latest else run.data_quality_status,
        delivery_status=latest.delivery_status if latest else run.delivery_status,
        written_at=checkpointed_at, evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({_ARTIFACT: sha256(canonical_bytes(selection)).hexdigest()}),
        resumable=True,
    )
    if payload is None:
        repository.stage_artifact(run.run_id, _ARTIFACT, canonical_bytes(selection))
    repository.checkpoint_if_current(run.run_id, checkpoint, len(stored.checkpoints))
    return selection


def observe_prior_research(
    selection: PriorResearchSelection,
    snapshots: Mapping[str, MarketSnapshot],
    observed_through: date,
) -> PriorResearchObservations:
    """Observe only eligible prior hypotheses against available completed bars."""
    packet = selection.packet
    if packet is None or selection.publication_origin is BriefOrigin.DETERMINISTIC_REDUCED:
        return PriorResearchObservations(observations=(), gates=(), evidence=())
    observations: list[PlanObservation] = []
    gates: list[GateResult] = []
    evidence: dict[str, EvidenceItem] = {}
    prior_evidence = {item.evidence_id: item for item in packet.evidence}
    for plan in sorted(packet.deterministic_plan_inputs, key=lambda item: item.plan_id):
        if plan.plan_status not in {PlanStatus.DRAFT, PlanStatus.REVIEW_REQUIRED}:
            continue
        snapshot = snapshots.get(plan.symbol)
        reference = prior_evidence.get(plan.entry_zone.upper.evidence_id)
        try:
            if snapshot is None or reference is None:
                raise ValueError("prior observation inputs are unavailable")
            result = observe_prior_plan(
                plan, snapshot.completed_daily_bars, observed_through,
            )
        except ValueError:
            gates.append(GateResult(
                gate_id="prior-observation-" + sha256(plan.plan_id.encode()).hexdigest()[:24],
                status=GateStatus.BLOCK, reason_code="PRIOR_PLAN_OBSERVATION_UNAVAILABLE",
                message=f"Completed-bar observation is unavailable for prior plan {plan.plan_id}",
                evidence_ids=(), capability=Capability.WATCHLIST_METRICS_AVAILABLE,
                rule_version="prior-observation-v1",
            ))
            continue
        observations.append(result)
        evidence[reference.evidence_id] = reference
    return PriorResearchObservations(
        observations=tuple(observations), gates=tuple(gates),
        evidence=tuple(evidence[key] for key in sorted(evidence)),
    )
