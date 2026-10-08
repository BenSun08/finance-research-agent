from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from finance_research_agent.application.packet_service import (
    PacketBudgetExceeded,
    build_research_packet,
)
from finance_research_agent.application.prompt_source import canonical_prompt_sha256
from finance_research_agent.domain.enums import (
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    RunType,
)
from finance_research_agent.domain.models import (
    ConfigurationSnapshot,
    EvidenceItem,
    RunContext,
    SourceObservation,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.setups import CandidateExclusion
from finance_research_agent.domain.types import FrozenMap

NOW = datetime(2026, 8, 26, 12, 45, tzinfo=UTC)


@pytest.fixture
def packet_inputs():
    run = RunContext(
        run_id="premarket-2026-08-26-r1",
        run_type=RunType.PREMARKET,
        market_date=date(2026, 8, 26),
        revision=1,
        invoked_at=NOW,
        evidence_cutoff_at=NOW,
        execution_status=ExecutionStatus.ANALYZING,
        data_quality_status=DataQualityStatus.DEGRADED,
        delivery_status=DeliveryStatus.MANUAL,
        configuration_snapshot=ConfigurationSnapshot(
            content_hash_sha256="a" * 64,
            file_hashes=FrozenMap({"watchlist.yaml": "b" * 64}),
            watchlist_version="1",
            regime_policy_version="1",
            setup_policy_version="1",
            risk_policy_version="1",
            source_policy_version="1",
        ),
        core_version="0.5.0.dev0",
        mcp_contract_version="0.1",
        plugin_version="0.1",
        skill_version="0.1",
        prompt_version=canonical_prompt_sha256(),
        report_template_version="0.1",
        schema_versions=FrozenMap({"run-context": "0.1"}),
    )

    def evidence(index: int, excerpt: str = "") -> EvidenceItem:
        source_id = f"source-{index:02d}"
        evidence_id = f"evidence-{index:02d}"
        return EvidenceItem(
            evidence_id=evidence_id,
            source=SourceObservation(
                observation_id=source_id,
                provider="fixture",
                source_url=f"https://example.test/{index}",
                source_hash_sha256=f"{index:064x}",
                observed_at=NOW,
                retrieved_at=NOW,
                content_type="text/plain",
                excerpt=excerpt,
                persistence_allowed=True,
                quality_flags=(),
            ),
            authority_tier=5,
            instrument_id="AAPL",
            event_time=None,
            published_time=NOW,
            structured_fields=FrozenMap({"subject": "AAPL", "field": "headline"}),
            citation_label=f"Fixture source {index}",
        )

    def as_kwargs(*, evidence_items=None, exclusions=()):
        return {
            "run": run,
            "evidence": tuple(evidence_items if evidence_items is not None else (evidence(0),)),
            "snapshots": {},
            "events": (),
            "metrics": (),
            "gates": (),
            "candidates": (),
            "exclusions": exclusions,
            "plans": (),
            "capabilities": (),
            "observations": (),
        }

    return {
        "run": run,
        "evidence": evidence,
        "as_kwargs": as_kwargs,
        "one_second": timedelta(seconds=1),
    }


def test_packet_rejects_evidence_after_frozen_cutoff(packet_inputs) -> None:
    late = packet_inputs["evidence"](0).model_copy(
        update={"published_time": packet_inputs["run"].evidence_cutoff_at + timedelta(seconds=1)},
    )
    with pytest.raises(ValueError, match="after evidence cutoff"):
        build_research_packet(
            **packet_inputs["as_kwargs"](evidence_items=(late,)),
            max_serialized_bytes=250_000,
        )


def test_packet_requires_a_cutoff_bound_by_evidence_freeze(packet_inputs) -> None:
    unbound_run = packet_inputs["run"].model_copy(update={"evidence_cutoff_at": None})

    with pytest.raises(ValueError, match="evidence cutoff has not been frozen"):
        build_research_packet(
            **(packet_inputs["as_kwargs"]() | {"run": unbound_run}),
            max_serialized_bytes=250_000,
        )


def test_packet_is_deeply_immutable_and_has_stable_hash(packet_inputs) -> None:
    first = build_research_packet(**packet_inputs["as_kwargs"](), max_serialized_bytes=250_000)
    second = build_research_packet(**packet_inputs["as_kwargs"](), max_serialized_bytes=250_000)
    assert first.packet_id == second.packet_id
    assert first.canonical_sha256 == second.canonical_sha256
    with pytest.raises(ValidationError):
        first.evidence = first.evidence + (packet_inputs["evidence"](0),)


def test_packet_retains_regime_result_without_synthesis_recalculation(packet_inputs) -> None:
    from finance_research_agent.domain.regime import RegimePolicy, calculate_regime

    run = packet_inputs["run"]
    regime = calculate_regime(
        {}, RegimePolicy(version=run.configuration_snapshot.regime_policy_version),
        run.evidence_cutoff_at,
    )
    packet = build_research_packet(
        **packet_inputs["as_kwargs"](), max_serialized_bytes=250_000, regime_result=regime
    )
    assert packet.regime_result.regime.value == "unknown"
    assert packet.regime_result.score is None
    assert packet.regime_result.unavailable_reasons
    assert ResearchPacket.model_validate_json(packet.model_dump_json()) == packet


@pytest.mark.parametrize("invalid", ["policy", "late"])
def test_packet_rejects_regime_from_a_different_policy_or_cutoff(packet_inputs, invalid) -> None:
    from dataclasses import replace

    from finance_research_agent.domain.regime import RegimePolicy, calculate_regime

    run = packet_inputs["run"]
    regime = calculate_regime(
        {}, RegimePolicy(version=run.configuration_snapshot.regime_policy_version),
        run.evidence_cutoff_at,
    )
    changes = {
        "policy": {"policy_version": "different"},
        "late": {"calculated_at": run.evidence_cutoff_at + timedelta(seconds=1)},
    }
    with pytest.raises(ValueError):
        build_research_packet(
            **packet_inputs["as_kwargs"](), max_serialized_bytes=250_000,
            regime_result=replace(regime, **changes[invalid]),
        )


def test_absent_regime_keeps_legacy_packet_bytes_readable(packet_inputs) -> None:
    packet = build_research_packet(**packet_inputs["as_kwargs"](), max_serialized_bytes=250_000)
    encoded = packet.model_dump_json()
    assert '"regime_result"' not in encoded
    assert ResearchPacket.model_validate_json(encoded).model_dump_json() == encoded
    # Independently generated with base 0519c81 in an isolated source archive.
    assert packet.canonical_sha256 == (
        "0ee01e75360a476966cac1244d281303335e0322bc99caa6408814eb69cb35d8"
    )
    assert packet.synthesis_constraints.serialized_bytes == 2150


def test_populated_regime_decimal_score_and_metrics_survive_packet_roundtrip(packet_inputs) -> None:
    from decimal import Decimal

    from finance_research_agent.domain.regime import RegimePolicy, calculate_regime
    from tests.support.synthetic_market import make_regime_case

    regime = calculate_regime(
        make_regime_case("risk-on").snapshots, RegimePolicy(version="1"), NOW
    )
    assert regime.regime.value == "permissive"
    assert regime.score == Decimal("100")
    assert len(regime.metrics) == 38
    packet = build_research_packet(
        **(packet_inputs["as_kwargs"]() | {"metrics": regime.metrics}),
        max_serialized_bytes=250_000, regime_result=regime,
    )
    restored = ResearchPacket.model_validate_json(packet.model_dump_json())
    assert restored.regime_result == regime
    assert restored.metrics == packet.metrics


@pytest.mark.parametrize("invalid", ["missing", "replaced"])
def test_packet_rejects_omitted_or_replaced_regime_metric(packet_inputs, invalid) -> None:
    from dataclasses import replace
    from decimal import Decimal

    from finance_research_agent.domain.regime import RegimePolicy, calculate_regime
    from tests.support.synthetic_market import make_regime_case

    regime = calculate_regime(
        make_regime_case("risk-on").snapshots, RegimePolicy(version="1"), NOW
    )
    metric = next(item for item in regime.metrics if item.value not in (None, Decimal("0")))
    metrics = {
        "missing": regime.metrics[1:],
        "replaced": tuple(
            replace(item, value=Decimal("0")) if item.metric_id == metric.metric_id else item
            for item in regime.metrics
        ),
    }[invalid]
    with pytest.raises(ValueError, match="regime metrics must be retained"):
        build_research_packet(
            **(packet_inputs["as_kwargs"]() | {"metrics": metrics}),
            max_serialized_bytes=250_000, regime_result=regime,
        )


def test_packet_rejects_content_replacement_with_stale_hash(packet_inputs) -> None:
    packet = build_research_packet(
        **packet_inputs["as_kwargs"](), max_serialized_bytes=250_000
    )
    altered = packet.model_copy(update={"evidence": ()})
    with pytest.raises(ValidationError, match="identity does not match canonical content"):
        ResearchPacket.model_validate(altered, strict=True)


def test_packet_model_rejects_late_evidence_even_outside_the_builder(packet_inputs) -> None:
    packet = build_research_packet(
        **packet_inputs["as_kwargs"](), max_serialized_bytes=250_000
    )
    late = packet_inputs["evidence"](0).model_copy(
        update={"published_time": packet.run.evidence_cutoff_at + timedelta(seconds=1)}
    )
    altered = packet.model_copy(update={"evidence": (late,)})
    with pytest.raises(ValidationError, match="after evidence cutoff"):
        ResearchPacket.model_validate(altered, strict=True)


def test_budget_trims_low_authority_discovery_excerpts_but_keeps_provenance(
    packet_inputs,
) -> None:
    evidence = tuple(
        packet_inputs["evidence"](index, "x" * 8191 + chr(ord("A") + index)) for index in range(14)
    )
    packet = build_research_packet(
        **packet_inputs["as_kwargs"](evidence_items=evidence),
        max_serialized_bytes=80_000,
    )
    assert packet.synthesis_constraints.truncated is True
    assert packet.synthesis_constraints.omitted_sections == ("low_authority_discovery_excerpts",)
    assert all(not item.source.excerpt for item in packet.evidence)
    assert packet.evidence[0].source.source_hash_sha256 == evidence[0].source.source_hash_sha256
    assert all(
        item.source.source_url == evidence[index].source.source_url
        for index, item in enumerate(packet.evidence)
    )


def test_packet_budget_fails_when_protected_content_cannot_fit(packet_inputs) -> None:
    with pytest.raises(PacketBudgetExceeded):
        build_research_packet(**packet_inputs["as_kwargs"](), max_serialized_bytes=1)


def test_budget_trims_verbose_non_factual_descriptions_last(packet_inputs) -> None:
    evidence = tuple(
        packet_inputs["evidence"](index).model_copy(
            update={
                "authority_tier": 1,
                "citation_label": f"Official source description {index} " + "detail " * 25,
            }
        )
        for index in range(20)
    )
    packet = build_research_packet(
        **packet_inputs["as_kwargs"](evidence_items=evidence),
        max_serialized_bytes=16_500,
    )
    assert packet.synthesis_constraints.truncated is True
    assert packet.synthesis_constraints.omitted_sections == (
        "verbose_non_factual_descriptions",
    )
    assert packet.evidence[0].source.source_url == evidence[0].source.source_url
    assert packet.evidence[0].source.source_hash_sha256 == evidence[0].source.source_hash_sha256
    assert packet.evidence[0].citation_label == packet.evidence[0].evidence_id


def test_packet_contains_r6_candidate_exclusions(packet_inputs) -> None:
    from finance_research_agent.domain.enums import GateStatus
    from finance_research_agent.domain.models import GateResult

    exclusion = CandidateExclusion(
        symbol="AAPL",
        reason_codes=("EVENT_RISK_BLOCK",),
        gates=(
            GateResult(
                gate_id="event-risk",
                status=GateStatus.BLOCK,
                reason_code="EVENT_RISK_BLOCK",
                message="Frozen event gate blocked the candidate.",
                evidence_ids=("evidence-00",),
                capability=Capability.PLAN_DRAFT_AVAILABLE,
                rule_version="r5",
            ),
        ),
    )
    second_exclusion = exclusion.model_copy(update={"symbol": "MSFT"})
    packet = build_research_packet(
        **packet_inputs["as_kwargs"](exclusions=(exclusion, second_exclusion)),
        max_serialized_bytes=250_000,
    )
    assert packet.candidate_exclusions == (exclusion, second_exclusion)


def test_packet_budget_never_trims_candidate_exclusions(packet_inputs) -> None:
    from finance_research_agent.domain.enums import GateStatus
    from finance_research_agent.domain.models import GateResult

    exclusion = CandidateExclusion(
        symbol="AAPL",
        reason_codes=("EVENT_RISK_BLOCK",),
        gates=(
            GateResult(
                gate_id="event-risk",
                status=GateStatus.BLOCK,
                reason_code="EVENT_RISK_BLOCK",
                message="Frozen event gate blocked the candidate.",
                evidence_ids=("evidence-00",),
                capability=Capability.PLAN_DRAFT_AVAILABLE,
                rule_version="r5",
            ),
        ),
    )
    baseline_packet = build_research_packet(
        **packet_inputs["as_kwargs"](),
        max_serialized_bytes=250_000,
    )
    with pytest.raises(PacketBudgetExceeded):
        build_research_packet(
            **packet_inputs["as_kwargs"](exclusions=(exclusion,)),
            max_serialized_bytes=baseline_packet.synthesis_constraints.serialized_bytes,
        )
