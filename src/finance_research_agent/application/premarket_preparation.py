"""Bounded composition from an initialized run to frozen synthesis input."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal, Self, cast

from pydantic import model_validator

from finance_research_agent.application.collection_bridge import (
    collected_snapshot_to_regime_input,
    collection_to_packet_inputs,
)
from finance_research_agent.application.collection_service import (
    collect_and_freeze_market_data_for_run,
)
from finance_research_agent.application.config_service import configuration_from_snapshot
from finance_research_agent.application.market_collection import (
    MarketCalendarUnavailable,
    resolve_completed_session_window,
)
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.ports import (
    MarketCalendarReadinessProvider,
    MarketDataProvider,
)
from finance_research_agent.application.preparation_service import stage_research_packet
from finance_research_agent.application.prior_research import (
    freeze_prior_research,
    observe_prior_research,
)
from finance_research_agent.application.publication_service import (
    PublicationRepository,
    publish_operational_report,
)
from finance_research_agent.application.quality_pipeline import (
    checkpoint_collected_market_data_quality,
)
from finance_research_agent.application.run_service import (
    PreparePremarketRunRequest,
    RunDependencies,
    prepare_premarket_run,
)
from finance_research_agent.application.run_state import compose_publication_context
from finance_research_agent.domain.eligibility import evaluate_instrument_eligibility
from finance_research_agent.domain.enums import (
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    GateStatus,
    InvocationType,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.events import assess_event_risk
from finance_research_agent.domain.market_calendar import NEW_YORK, RunWindowDecision, format_run_id
from finance_research_agent.domain.models import (
    CapabilityState,
    EvidenceItem,
    GateResult,
    MissedRunRecord,
    ProviderFailure,
    PublishedArtifact,
    RunContext,
    StoredRun,
    StrictModel,
)
from finance_research_agent.domain.packets import PacketBudgetExceeded, ResearchPacket
from finance_research_agent.domain.regime import Regime, RegimeResult, calculate_regime
from finance_research_agent.domain.setups import CandidateExclusion, assess_setups, setup_gate
from finance_research_agent.domain.types import FrozenMap, canonical_bytes, utc_datetime

_RUN_DURATION = timedelta(minutes=15)
_MARKET_AUTHORITY_TIER = 2
_DEFAULT_PACKET_BYTES = 8_000_000


def _unknown_regime_projection(
    regime: RegimeResult,
    evidence: tuple[EvidenceItem, ...],
    capabilities: tuple[CapabilityState, ...],
    broad_symbols: tuple[str, ...],
) -> tuple[tuple[CapabilityState, ...], GateResult | None]:
    """Disable usable regime/plan claims when required broad data is absent."""
    if regime.regime is not Regime.UNKNOWN:
        return capabilities, None
    missing_evidence_ids = tuple(
        dict.fromkeys(
            item.evidence_id
            for item in evidence
            if item.source.provider == "alpaca"
            and item.structured_fields.get("outcome") == "DAILY_BARS"
            and item.structured_fields.get("requested_symbol") in broad_symbols
            and item.structured_fields.get("error_code") == ErrorCode.PROVIDER_NO_DATA.value
        )
    )
    if not missing_evidence_ids:
        return capabilities, None

    projected: list[CapabilityState] = []
    affected = {
        Capability.REGIME_CLASSIFICATION_AVAILABLE,
        Capability.PLAN_DRAFT_AVAILABLE,
    }
    for state in capabilities:
        if state.capability not in affected:
            projected.append(state)
            continue
        projected.append(CapabilityState(
            capability=state.capability,
            available=False,
            reason_codes=tuple(dict.fromkeys(
                (*state.reason_codes, ErrorCode.PROVIDER_NO_DATA)
            )),
            evidence_ids=tuple(dict.fromkeys((*state.evidence_ids, *missing_evidence_ids))),
        ))
    disclosure = GateResult(
        gate_id="regime-classification-unknown",
        status=GateStatus.BLOCK,
        reason_code=ErrorCode.PROVIDER_NO_DATA.value,
        message=(
            "Regime classification is UNKNOWN because required broad-market inputs "
            "are unavailable; no usable classification or new plan can be drafted."
        ),
        evidence_ids=missing_evidence_ids,
        capability=Capability.REGIME_CLASSIFICATION_AVAILABLE,
        rule_version=regime.formula_version,
    )
    return tuple(projected), disclosure


def _stale_premarket_price_gates(evidence: tuple[EvidenceItem, ...]) -> tuple[GateResult, ...]:
    """Disclose stale current-price failures beside the affected sizing capability."""
    gates: list[GateResult] = []
    for item in evidence:
        symbol = item.structured_fields.get("requested_symbol")
        if (
            item.source.provider != "alpaca"
            or item.structured_fields.get("outcome") != "PREMARKET_PRICE"
            or item.structured_fields.get("error_code") != ErrorCode.STALE_DATA.value
            or not isinstance(symbol, str)
        ):
            continue
        gates.append(GateResult(
            gate_id=(
                f"{symbol}-stale-current-price-"
                f"{sha256(item.evidence_id.encode('utf-8')).hexdigest()[:24]}"
            ),
            status=GateStatus.BLOCK,
            reason_code=ErrorCode.STALE_DATA.value,
            message=(
                f"Current premarket price for {symbol} is stale; "
                "position sizing is unavailable."
            ),
            evidence_ids=(item.evidence_id,),
            capability=Capability.POSITION_SIZING_AVAILABLE,
            rule_version="application-current-price-v1",
        ))
    return tuple(gates)


class PreparedPremarketRunResult(StrictModel):
    """Explicit immutable handoff; transport success alone never authorizes synthesis."""

    outcome: Literal["PACKET_READY", "PUBLISHED", "SKIPPED"]
    window_decision: RunWindowDecision
    stored_run: StoredRun | None = None
    research_packet: ResearchPacket | None = None
    publication: PublishedArtifact | None = None
    failure_code: ErrorCode | None = None

    @model_validator(mode="after")
    def coherent_handoff(self) -> Self:
        stored = self.stored_run
        packet = self.research_packet
        if self.outcome == "SKIPPED":
            if self.window_decision.should_run or any(
                value is not None for value in (stored, packet, self.publication, self.failure_code)
            ):
                raise ValueError("skipped preparation cannot carry run artifacts")
            return self
        if stored is None:
            raise ValueError("prepared result requires a stored run")
        if (
            self.window_decision.market_date != stored.run.market_date
            or self.window_decision.delivery_status is not stored.run.delivery_status
        ):
            raise ValueError("prepared window differs from the stored run")
        if self.outcome == "PUBLISHED":
            if (
                not stored.published
                or packet is not None
                or self.publication is None
                or self.publication.run_id != stored.run_id
            ):
                raise ValueError("published preparation requires the matching publication only")
            return self
        if (
            stored.published
            or packet is None
            or self.publication is not None
            or self.failure_code is not None
            or not stored.checkpoints
        ):
            raise ValueError("packet handoff requires an unpublished frozen packet only")
        latest = stored.checkpoints[-1]
        expected = compose_publication_context(stored.run, latest).model_copy(
            update={"execution_status": ExecutionStatus.AWAITING_SYNTHESIS}
        )
        if (
            latest.stage not in {"AWAITING_SYNTHESIS", "VALIDATING"}
            or packet.run != expected
            or packet.run.data_quality_status is DataQualityStatus.FAIL
            or stored.evidence_cutoff_at != packet.run.evidence_cutoff_at
            or latest.artifact_hashes.get("research_packet")
            != sha256(canonical_bytes(packet)).hexdigest()
        ):
            raise ValueError("packet handoff differs from the frozen run checkpoint")
        return self


def _published_result(
    repository: PublicationRepository, stored: StoredRun, decision: RunWindowDecision
) -> PreparedPremarketRunResult:
    artifact = repository.get_published_artifact(stored.run_id)
    if artifact is None:
        raise ValueError("published run is missing its verified publication")
    bundle = repository.load_published_bundle(stored.run_id)
    if bundle is None:
        raise ValueError("published run is missing its verified bundle")
    published_context = (
        compose_publication_context(stored.run, stored.checkpoints[-1])
        if stored.checkpoints
        else stored.run
    )
    if (
        not stored.published
        or artifact.run_id != stored.run_id
        or published_context.execution_status is not ExecutionStatus.PUBLISHED
        or published_context != bundle.run
    ):
        raise ValueError("published stored context differs from immutable bundle")
    reason = bundle.bundle.get("failure_code")
    return PreparedPremarketRunResult(
        outcome="PUBLISHED",
        window_decision=decision,
        stored_run=stored,
        publication=artifact,
        failure_code=ErrorCode(reason) if isinstance(reason, str) else None,
    )


def _operational_failure(
    repository: PublicationRepository,
    stored: StoredRun,
    decision: RunWindowDecision,
    reason: ErrorCode,
    checkpointed_at: datetime,
) -> PreparedPremarketRunResult:
    latest = stored.checkpoints[-1]
    if latest.data_quality_status is not DataQualityStatus.FAIL:
        digest = repository.stage_artifact(
            stored.run_id, "operational_reason", reason.value.encode("ascii")
        )
        failure = latest.model_copy(
            update={
                "stage": "PREPARATION_FAILED",
                "data_quality_status": DataQualityStatus.FAIL,
                "written_at": checkpointed_at,
                "resumable": False,
                "artifact_hashes": FrozenMap(
                    {
                        **dict(latest.artifact_hashes),
                        "operational_reason": digest,
                    }
                ),
            }
        )
        repository.checkpoint_if_current(stored.run_id, failure, len(stored.checkpoints))
        stored = _load_required(repository, stored.run_id)
    run = compose_publication_context(stored.run, stored.checkpoints[-1])
    publish_operational_report(repository, run, reason, checkpointed_at)
    return _published_result(repository, _load_required(repository, stored.run_id), decision)


def _load_required(repository: PublicationRepository, run_id: str) -> StoredRun:
    stored = repository.load(run_id)
    if stored is None:
        raise ValueError("prepared run could not be reloaded")
    return stored


def _scope_gates(symbol: str, gates: tuple[GateResult, ...]) -> tuple[GateResult, ...]:
    return tuple(
        gate.model_copy(update={"gate_id": f"{symbol}-{index}-{gate.gate_id}"})
        for index, gate in enumerate(gates)
    )


def prepare_research_packet(
    request: PreparePremarketRunRequest,
    dependencies: RunDependencies,
    *,
    max_packet_bytes: int = _DEFAULT_PACKET_BYTES,
    market_data_for_run: Callable[[RunContext, datetime], MarketDataProvider] | None = None,
) -> PreparedPremarketRunResult:
    """Prepare one current-source revision or reuse its exact frozen artifacts."""
    if type(max_packet_bytes) is not int or max_packet_bytes <= 0:
        raise ValueError("packet byte limit must be a positive integer")
    if request.invocation is InvocationType.SCHEDULED and request.requested_revision is not None:
        raise ValueError("scheduled runs cannot request an explicit revision")
    if dependencies.event_providers:
        raise ValueError("event providers require a separately approved source scope")
    repository = cast(PublicationRepository, dependencies.run_repository)
    # The initializer deliberately rejects published manual resumes. The public
    # operation instead returns a verified existing receipt for explicit identity.
    if request.requested_revision is not None:
        day = request.market_date or dependencies.clock.now_utc().astimezone(NEW_YORK).date()
        existing = repository.load(format_run_id(day, request.requested_revision))
        if existing is not None and existing.published:
            from finance_research_agent.domain.market_calendar import resolve_run_window

            decision = resolve_run_window(
                existing.run.invoked_at,
                dependencies.calendar,
                day,
                InvocationType.MANUAL
                if existing.run.delivery_status is DeliveryStatus.MANUAL
                else InvocationType.SCHEDULED,
            )
            if decision.publish_missed_report:
                decision = replace(decision, publish_missed_report=False)
            return _published_result(repository, existing, decision)
    initialized = prepare_premarket_run(request, dependencies)
    stored = initialized.stored_run
    decision = initialized.window_decision
    if stored is None:
        if decision.missed_record_only:
            regular_close_at = dependencies.calendar.session_open_close(
                decision.market_date
            )[1]
            repository.record_missed_run(
                MissedRunRecord(
                    market_date=decision.market_date,
                    detected_at=dependencies.clock.now_utc(),
                    regular_close_at=utc_datetime(regular_close_at),
                    reason_code="MISSED_WINDOW",
                )
            )
        return PreparedPremarketRunResult(outcome="SKIPPED", window_decision=decision)
    if stored.published:
        return _published_result(repository, stored, decision)
    if decision.publish_missed_report:
        return _operational_failure(
            repository,
            stored,
            decision,
            ErrorCode.MISSED_WINDOW,
            dependencies.clock.now_utc(),
        )
    latest = stored.checkpoints[-1]
    # Reuse an existing packet before consulting the clock, configuration, or
    # providers. This also completes a crash between packet staging/checkpoint.
    payload = repository.read_staged_artifact(stored.run_id, "research_packet")
    if payload is not None:
        if "research_packet" in latest.artifact_hashes and (
            sha256(payload).hexdigest() != latest.artifact_hashes["research_packet"]
        ):
            raise ValueError("staged research packet differs from checkpoint hash")
        packet = ResearchPacket.model_validate_json(payload, strict=True)
        if canonical_bytes(packet) != payload:
            raise ValueError("staged research packet is not canonical")
        stage_research_packet(repository, packet, max(latest.written_at, packet.run.invoked_at))
        return PreparedPremarketRunResult(
            outcome="PACKET_READY",
            window_decision=decision,
            stored_run=_load_required(repository, stored.run_id),
            research_packet=packet,
        )
    if latest.stage in {"AWAITING_SYNTHESIS", "VALIDATING"}:
        raise ValueError("prepared checkpoint is missing its research packet")
    reason_bytes = repository.read_staged_artifact(stored.run_id, "operational_reason")
    if reason_bytes is not None or latest.stage in {"PUBLISHED", "PREPARATION_FAILED"}:
        if reason_bytes is None:
            raise ValueError("publication recovery requires its operational reason")
        if "operational_reason" in latest.artifact_hashes and (
            sha256(reason_bytes).hexdigest() != latest.artifact_hashes["operational_reason"]
        ):
            raise ValueError("operational reason differs from its checkpoint hash")
        return _operational_failure(
            repository,
            stored,
            decision,
            ErrorCode(reason_bytes.decode("ascii")),
            dependencies.clock.now_utc(),
        )

    try:
        closed_at = utc_datetime(
            dependencies.calendar.session_open_close(stored.run.market_date)[1]
        )
    except Exception:
        return _operational_failure(
            repository,
            stored,
            decision,
            ErrorCode.MARKET_CALENDAR_UNAVAILABLE,
            dependencies.clock.now_utc(),
        )
    deadline = min(stored.run.invoked_at + _RUN_DURATION, closed_at)
    started_ns = dependencies.monotonic_ns()

    def check_deadline() -> None:
        if dependencies.clock.now_utc() >= deadline or (
            dependencies.monotonic_ns() - started_ns >= 900_000_000_000
        ):
            raise TimeoutError("premarket run deadline exceeded")

    try:
        check_deadline()
        prior = freeze_prior_research(stored.run, repository, dependencies.clock.now_utc())
        provider = (
            dependencies.market_data
            if market_data_for_run is None
            or (
                stored.evidence_cutoff_at is not None
                and repository.read_staged_artifact(stored.run_id, "data_quality") is not None
            )
            else market_data_for_run(stored.run, deadline)
        )
        collected = collect_and_freeze_market_data_for_run(
            stored.run, repository, provider, dependencies.clock, dependencies.calendar
        )
        if any(
            isinstance(value, ProviderFailure) and value.error_code is ErrorCode.DEADLINE_EXCEEDED
            for item in collected.collection.symbols
            for value in (item.instrument, item.daily_bars, item.premarket_observation)
        ):
            raise TimeoutError("provider run deadline exceeded")
        check_deadline()
        evaluated = checkpoint_collected_market_data_quality(
            repository,
            collected,
            provider,
            cast(MarketCalendarReadinessProvider, dependencies.calendar),
            checkpointed_at=dependencies.clock.now_utc(),
        )
        stored = evaluated.stored_run
        quality = evaluated.quality
        if quality.status is DataQualityStatus.FAIL:
            return _operational_failure(
                repository,
                stored,
                decision,
                quality.global_reason_codes[0],
                dependencies.clock.now_utc(),
            )
        check_deadline()
        configuration = configuration_from_snapshot(stored.run.configuration_snapshot)
        inputs = collection_to_packet_inputs(
            collected.collection, authority_tier=_MARKET_AUTHORITY_TIER
        )
        prior_observations = observe_prior_research(
            prior,
            inputs.market,
            resolve_completed_session_window(
                dependencies.calendar, before=stored.run.market_date, session_count=1
            )[0],
        )
        cutoff = stored.run.require_evidence_cutoff()
        regime = calculate_regime(
            {
                symbol: collected_snapshot_to_regime_input(snapshot, cutoff)
                for symbol, snapshot in inputs.market.items()
                if snapshot.completed_daily_bars
            },
            configuration.regime,
            cutoff,
        )
        gates: list[GateResult] = list(prior_observations.gates)
        exclusions: list[CandidateExclusion] = []
        for item in configuration.watchlist.items:
            snapshot = inputs.market.get(item.symbol)
            if snapshot is None:
                missing = _scope_gates(
                    item.symbol,
                    (setup_gate("REQUIRED_DATA_MISSING", "instrument identity is unavailable"),),
                )
                gates.extend(missing)
                exclusions.append(
                    CandidateExclusion(
                        symbol=item.symbol,
                        reason_codes=("REQUIRED_DATA_MISSING",),
                        gates=missing,
                    )
                )
                continue
            eligibility = evaluate_instrument_eligibility(
                instrument=snapshot.instrument,
                watchlist_item=item,
                snapshot=snapshot,
                setup_policy=configuration.setup,
            )
            # No approved event source exists. Its absence is a real blocking
            # assessment, never a synthetic healthy calendar or a safe empty feed.
            event = assess_event_risk(
                instrument=snapshot.instrument,
                events=(),
                plan_expires_at=deadline,
                evidence_cutoff_at=cutoff,
            )
            assessed = assess_setups(
                snapshot=snapshot,
                benchmark=inputs.market.get(item.benchmark_symbol or ""),
                sector_proxy=inputs.market.get(item.sector_proxy_symbol or ""),
                setup_policy=configuration.setup,
                evidence_cutoff_at=cutoff,
                eligibility_gates=eligibility,
                event_assessment=event,
                data_quality=quality,
            )
            if assessed.setups:
                raise ValueError("current source scope cannot authorize plan-producing setups")
            for exclusion in assessed.exclusions:
                scoped = _scope_gates(item.symbol, exclusion.gates)
                gates.extend(scoped)
                exclusions.append(exclusion.model_copy(update={"gates": scoped}))
        context = compose_publication_context(stored.run, stored.checkpoints[-1]).model_copy(
            update={"execution_status": ExecutionStatus.AWAITING_SYNTHESIS}
        )
        packet_evidence = (*inputs.evidence, *prior_observations.evidence)
        capabilities, regime_disclosure = _unknown_regime_projection(
            regime,
            inputs.evidence,
            quality.capabilities,
            configuration.regime.broad_symbols,
        )
        if regime_disclosure is not None:
            gates.append(regime_disclosure)
        gates.extend(_stale_premarket_price_gates(inputs.evidence))
        packet = build_research_packet(
            run=context,
            evidence=packet_evidence,
            snapshots=inputs.market,
            events=(),
            metrics=regime.metrics,
            gates=gates,
            candidates=(),
            exclusions=exclusions,
            plans=(),
            capabilities=capabilities,
            observations=prior_observations.observations,
            max_serialized_bytes=max_packet_bytes,
            regime_result=regime,
        )
        check_deadline()
        stage_research_packet(repository, packet, dependencies.clock.now_utc())
        return PreparedPremarketRunResult(
            outcome="PACKET_READY",
            window_decision=decision,
            stored_run=_load_required(repository, stored.run_id),
            research_packet=packet,
        )
    except (TimeoutError, PacketBudgetExceeded, MarketCalendarUnavailable) as error:
        reason = (
            ErrorCode.DEADLINE_EXCEEDED
            if isinstance(error, TimeoutError)
            else ErrorCode.MARKET_CALENDAR_UNAVAILABLE
            if isinstance(error, MarketCalendarUnavailable)
            else ErrorCode.INTERNAL_ERROR
        )
        return _operational_failure(
            repository,
            _load_required(repository, stored.run_id),
            decision,
            reason,
            dependencies.clock.now_utc(),
        )
