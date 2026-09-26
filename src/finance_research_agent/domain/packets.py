"""Immutable bounded synthesis packet contracts."""

import json
from hashlib import sha256
from typing import Annotated, Self

from pydantic import Field, model_validator

from finance_research_agent.domain.metrics import MetricResult
from finance_research_agent.domain.models import (
    CapabilityState,
    EventRecord,
    EvidenceItem,
    GateResult,
    Identifier,
    MarketSnapshot,
    RunContext,
    Sha256,
    StrictModel,
)
from finance_research_agent.domain.observations import PlanObservation
from finance_research_agent.domain.plans import TradePlanDraft
from finance_research_agent.domain.scoring import SetupCandidate
from finance_research_agent.domain.setups import CandidateExclusion
from finance_research_agent.domain.types import FrozenMap


class SynthesisConstraints(StrictModel):
    """Deterministic limits and disclosure for content removed from a packet."""

    max_serialized_bytes: Annotated[int, Field(gt=0)]
    serialized_bytes: Annotated[int, Field(gt=0)]
    truncated: bool = False
    omitted_sections: tuple[Identifier, ...] = ()
    omitted_bytes: FrozenMap[Identifier, int] = Field(default_factory=lambda: FrozenMap({}))

    @model_validator(mode="after")
    def _consistent_omissions(self) -> Self:
        if self.truncated != bool(self.omitted_sections):
            raise ValueError("truncated must match omitted sections")
        if set(self.omitted_sections) != set(self.omitted_bytes):
            raise ValueError("every omitted section requires one byte count")
        if any(value < 0 for value in self.omitted_bytes.values()):
            raise ValueError("omitted byte counts must be nonnegative")
        if self.serialized_bytes > self.max_serialized_bytes:
            raise ValueError("serialized packet exceeds its declared maximum")
        return self


class ResearchPacket(StrictModel):
    """Deeply immutable, bounded synthesis input derived from frozen Product A values."""

    packet_id: Identifier
    canonical_sha256: Sha256
    run: RunContext
    evidence: tuple[EvidenceItem, ...]
    market: FrozenMap[Identifier, MarketSnapshot]
    events: tuple[EventRecord, ...]
    metrics: tuple[MetricResult, ...]
    gates: tuple[GateResult, ...]
    candidates: tuple[SetupCandidate, ...]
    candidate_exclusions: tuple[CandidateExclusion, ...]
    deterministic_plan_inputs: tuple[TradePlanDraft, ...]
    capability_states: tuple[CapabilityState, ...]
    prior_plan_observations: tuple[PlanObservation, ...]
    synthesis_constraints: SynthesisConstraints

    @model_validator(mode="after")
    def _verify_identity_and_size(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"packet_id", "canonical_sha256"})
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        digest = sha256(canonical).hexdigest()
        if self.canonical_sha256 != digest or self.packet_id != f"packet-{digest}":
            raise ValueError("research packet identity does not match canonical content")
        serialized = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if self.synthesis_constraints.serialized_bytes != len(serialized):
            raise ValueError("research packet serialized byte count is incorrect")
        return self



class PacketBudgetExceeded(ValueError):
    """Protected packet content cannot fit within the configured byte budget."""
