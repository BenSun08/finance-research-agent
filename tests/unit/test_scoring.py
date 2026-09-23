import json
from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from finance_research_agent.domain.enums import DataQualityStatus, GateStatus, PlanStatus
from finance_research_agent.domain.events import EventAssessment
from finance_research_agent.domain.quality import evaluate_data_quality
from finance_research_agent.domain.regime import (
    Regime,
    RegimeComponentReason,
    RegimeComponentResult,
    RegimeComponentState,
    RegimePolicy,
    RegimeResult,
)
from finance_research_agent.domain.scoring import (
    BlockedBeforeScoring,
    CorrelationEvidence,
    ScoreComponent,
    ScoreComponentName,
    rank_candidates,
    score_candidate,
)
from finance_research_agent.domain.setups import detect_setups
from tests.unit.test_setups import _CUTOFF, _FIXTURES, _block, _load_context, _snapshot


def _setup(symbol="AAA"):
    context = _load_context("valid-breakout.json")
    if symbol != "AAA":
        original = context["snapshot"]
        context["snapshot"] = _snapshot(
            symbol, tuple(str(b.close) for b in original.completed_daily_bars), volume=250000
        )
    return detect_setups(**context)[0]


def _score_context(setup):
    return dict(
        setup_policy=setup.policy,
        eligibility_gates=setup.eligibility_gates,
        event_assessment=setup.event_assessment,
        data_quality=setup.data_quality,
        regime_policy_version=RegimePolicy().version,
    )


def _uniform(quality):
    def evaluate(setup, events, evidence):
        return tuple(
            ScoreComponent(
                name=name,
                quality=Decimal(quality),
                calculation="Controlled normalized quality",
                metric_ids=(setup.metrics[0].metric_id,),
                evidence_ids=("evidence-aaa-10",),
                evidence_cutoff_at=setup.evidence_cutoff_at,
            )
            for name in ScoreComponentName
        )

    return evaluate


def _candidate(symbol="AAA", quality="0.8"):
    setup = _setup(symbol)
    return score_candidate(setup, **_score_context(setup), component_evaluator=_uniform(quality))


def _regime(kind=Regime.PERMISSIVE):
    state = (
        RegimeComponentState.UNAVAILABLE
        if kind is Regime.UNKNOWN
        else RegimeComponentState.NEGATIVE
        if kind is Regime.DEFENSIVE
        else RegimeComponentState.MIXED
        if kind is Regime.NEUTRAL
        else RegimeComponentState.POSITIVE
    )
    policy = RegimePolicy()
    components = tuple(
        RegimeComponentResult(
            component=component,
            state=state,
            weight=weight,
            weighted_score=None
            if kind is Regime.UNKNOWN
            else -weight
            if kind is Regime.DEFENSIVE
            else Decimal(0)
            if kind is Regime.NEUTRAL
            else weight,
            metric_ids=(),
            reason_code=RegimeComponentReason.POSITIVE_RULE_MATCHED,
            quality_flags=(),
        )
        for component, weight in policy.component_weights
    )
    return RegimeResult(
        schema_version="regime-result-v1",
        result_id="regime-fixture",
        regime=kind,
        score=None
        if kind is Regime.UNKNOWN
        else sum((c.weighted_score for c in components), Decimal(0)),
        components=components,
        metrics=(),
        critical_stress=False,
        critical_stress_reasons=(),
        policy_version=policy.version,
        formula_version="fixture",
        calculated_at=_CUTOFF,
        input_snapshot_ids=(),
        quality_flags=(),
        unavailable_reasons=(),
    )


def test_blocked_candidate_is_rejected_before_any_score_component():
    setup = _setup()
    calls = []

    def never(*args):
        calls.append(args)
        raise AssertionError("component evaluation must not run")

    with pytest.raises(BlockedBeforeScoring) as failure:
        score_candidate(
            setup,
            **{**_score_context(setup), "eligibility_gates": (_block(),)},
            component_evaluator=never,
        )
    assert calls == []
    assert failure.value.exclusion.gates[0] == _block()
    assert failure.value.exclusion.symbol == "AAA"


def test_original_gate_block_cannot_be_replaced_by_new_empty_gates():
    setup = _setup().model_copy(update={"eligibility_gates": (_block(),)})
    calls = []

    def never(*args):
        calls.append(args)
        raise AssertionError("component evaluation must not run")

    with pytest.raises(BlockedBeforeScoring):
        score_candidate(
            setup,
            **{**_score_context(setup), "eligibility_gates": ()},
            component_evaluator=never,
        )
    assert calls == []


def test_frozen_blocked_event_cannot_be_replaced_before_component_evaluation():
    setup = _setup().model_copy(
        update={
            "event_assessment": EventAssessment(
                plan_status=PlanStatus.BLOCKED,
                gates=(_block(),),
            )
        }
    )
    calls = []

    def never(*args):
        calls.append(args)
        raise AssertionError("component evaluation must not run")

    with pytest.raises(ValueError, match="event assessment"):
        score_candidate(
            setup,
            **{
                **_score_context(setup),
                "event_assessment": EventAssessment(plan_status=PlanStatus.DRAFT, gates=()),
            },
            component_evaluator=never,
        )
    assert calls == []


def test_frozen_failed_data_quality_cannot_be_replaced_before_component_evaluation():
    failed_quality = evaluate_data_quality(source_health=())
    assert failed_quality.status is DataQualityStatus.FAIL
    setup = _setup().model_copy(update={"data_quality": failed_quality})
    calls = []

    def never(*args):
        calls.append(args)
        raise AssertionError("component evaluation must not run")

    with pytest.raises(ValueError, match="data quality"):
        score_candidate(
            setup,
            **{
                **_score_context(setup),
                "data_quality": _score_context(_setup())["data_quality"],
            },
            component_evaluator=never,
        )
    assert calls == []


def test_exact_positive_weights_and_separately_visible_penalties():
    candidate = _candidate()
    assert tuple(c.weight for c in candidate.components) == tuple(
        map(Decimal, (25, 20, 20, 15, 10, 10))
    )
    assert tuple(c.points for c in candidate.components) == tuple(
        map(Decimal, (20, 16, 16, 12, 8, 8))
    )
    assert candidate.positive_score == Decimal("80")
    assert tuple(p.name.value for p in candidate.penalties) == (
        "EXTENSION_PENALTY",
        "EVENT_UNCERTAINTY_PENALTY",
        "CORRELATION_CONCENTRATION_PENALTY",
        "DATA_QUALITY_PENALTY",
    )
    assert candidate.total_score == candidate.positive_score - sum(
        p.points for p in candidate.penalties
    )
    assert all(c.evidence_cutoff_at == _CUTOFF and c.evidence_ids for c in candidate.components)


def test_altered_positive_setup_weights_fail_closed_at_scoring_boundary():
    setup = _setup()
    altered_policy = setup.policy.model_copy(
        update={"score_weights": tuple(map(Decimal, (30, 15, 20, 15, 10, 10)))}
    )
    altered_setup = setup.model_copy(
        update={"policy": altered_policy, "policy_hash": setup.policy_hash}
    )

    with pytest.raises(ValueError, match="unsupported R6 setup policy"):
        score_candidate(
            altered_setup,
            **{**_score_context(altered_setup), "setup_policy": altered_policy},
        )


@pytest.mark.parametrize(
    "regime,quality,selected",
    [
        (Regime.PERMISSIVE, "0.6999", False),
        (Regime.PERMISSIVE, "0.7", True),
        (Regime.NEUTRAL, "0.7999", False),
        (Regime.NEUTRAL, "0.8", True),
        (Regime.DEFENSIVE, "1", False),
        (Regime.UNKNOWN, "1", False),
    ],
)
def test_threshold_boundaries_and_blocking_regimes(regime, quality, selected):
    ranked = rank_candidates((_candidate(quality=quality),), _regime(regime), RegimePolicy())
    assert len(ranked) == 1
    assert ranked[0].selected_for_plan is selected
    assert ranked[0].executive_highlight is selected
    assert bool(ranked[0].selection_reasons) is not selected


def test_ranking_uses_exact_tie_order_and_caps_five():
    fixture = json.loads((_FIXTURES / "six-ranked-candidates.json").read_text())
    candidates = tuple(
        _candidate(symbol, fixture["quality"]) for symbol in fixture["input_symbols"]
    )
    ranked = rank_candidates(candidates, _regime(), RegimePolicy())
    assert [c.symbol for c in ranked] == fixture["expected_symbols"]
    assert [c.selected_for_plan for c in ranked] == fixture["expected_selected"]
    assert [c.executive_highlight for c in ranked] == fixture["expected_highlights"]
    assert rank_candidates(tuple(reversed(candidates)), _regime(), RegimePolicy()) == ranked
    assert "PLAN_CAP" in ranked[-1].selection_reasons


@pytest.mark.parametrize("field", ["setup", "relative", "rr", "data", "liquidity"])
def test_every_tie_break_precedes_alphabetical_ticker(field):
    low, high = _candidate("AAA"), _candidate("ZZZ")
    # Keep the total exactly equal while changing one successive tie-break field.
    if field in {"setup", "relative", "rr"}:
        target = {"setup": 0, "relative": 2, "rr": 3}[field]
        components = list(high.components)
        delta = Decimal("0.01")
        components[target] = components[target].model_copy(
            update={"quality": Decimal("0.8") + delta}
        )
        components[1] = components[1].model_copy(
            update={
                "quality": Decimal("0.8") - delta * components[target].weight / Decimal(20),
            }
        )
        high = high.model_copy(update={"components": tuple(components)})
    elif field == "data":
        low = low.model_copy(update={"data_quality_rank": Decimal("0.5")})
    else:
        high = high.model_copy(update={"liquidity_rank": high.liquidity_rank + 1})
    ranked = rank_candidates((low, high), _regime(), RegimePolicy())
    assert tuple(c.symbol for c in ranked) == ("ZZZ", "AAA")


def test_highly_correlated_qualifiers_remain_visible_as_secondary_alternatives():
    correlation = CorrelationEvidence(
        left_symbol="AAA",
        right_symbol="BBB",
        coefficient=Decimal("0.95"),
        evidence_ids=("ev-correlation",),
        observed_at=_CUTOFF,
        method_version="fixture-1",
        exposure_description="Shared technology factor exposure",
    )
    ranked = rank_candidates(
        (_candidate("BBB"), _candidate("AAA")),
        _regime(),
        RegimePolicy(),
        correlations=(correlation,),
    )
    assert ranked[0].symbol == "AAA" and ranked[0].selected_for_plan
    assert ranked[1].secondary_alternative and not ranked[1].selected_for_plan
    assert ranked[1].primary_symbol == "AAA"
    assert "DUPLICATE_EXPOSURE" in ranked[1].selection_reasons
    penalty = ranked[1].penalties[2]
    assert penalty.points == Decimal("10") and penalty.evidence_ids == ("ev-correlation",)
    assert rank_candidates(ranked, _regime(), RegimePolicy(), correlations=(correlation,)) == ranked


@pytest.mark.parametrize(
    "correlations",
    [
        (
            ("AAA", "BBB"),
            ("BBB", "AAA"),
        ),
        (
            ("BBB", "AAA"),
            ("AAA", "BBB"),
        ),
    ],
)
def test_conflicting_duplicate_unordered_correlations_fail_closed(correlations):
    evidence = tuple(
        CorrelationEvidence(
            left_symbol=left,
            right_symbol=right,
            coefficient=Decimal(coefficient),
            evidence_ids=(f"ev-{index}",),
            observed_at=_CUTOFF,
            method_version="fixture-1",
            exposure_description="Conflicting pair record",
        )
        for index, ((left, right), coefficient) in enumerate(
            zip(correlations, ("0.95", "0.91"), strict=True)
        )
    )

    with pytest.raises(ValueError, match="duplicate unordered correlation pair"):
        rank_candidates(
            (_candidate("AAA"), _candidate("BBB")),
            _regime(),
            RegimePolicy(),
            correlations=evidence,
        )


def test_self_correlation_pair_is_rejected():
    correlation = CorrelationEvidence(
        left_symbol="AAA",
        right_symbol="AAA",
        coefficient=Decimal("0.95"),
        evidence_ids=("ev-self",),
        observed_at=_CUTOFF,
        method_version="fixture-1",
        exposure_description="Self-correlation is invalid",
    )

    with pytest.raises(ValueError, match="correlation pair must contain distinct symbols"):
        rank_candidates(
            (_candidate("AAA"),), _regime(), RegimePolicy(), correlations=(correlation,)
        )


def test_connected_correlation_group_has_one_pre_penalty_primary():
    correlations = (
        CorrelationEvidence(
            left_symbol="AAA",
            right_symbol="BBB",
            coefficient=Decimal("0.95"),
            evidence_ids=("ev-aaa-bbb",),
            observed_at=_CUTOFF,
            method_version="fixture-1",
            exposure_description="AAA and BBB share factor exposure",
        ),
        CorrelationEvidence(
            left_symbol="BBB",
            right_symbol="CCC",
            coefficient=Decimal("0.95"),
            evidence_ids=("ev-bbb-ccc",),
            observed_at=_CUTOFF,
            method_version="fixture-1",
            exposure_description="BBB and CCC share factor exposure",
        ),
    )
    ranked = rank_candidates(
        (_candidate("CCC", "0.95"), _candidate("BBB", "0.99"), _candidate("AAA", "1")),
        _regime(),
        RegimePolicy(),
        correlations=correlations,
    )

    assert [candidate.symbol for candidate in ranked] == ["AAA", "BBB", "CCC"]
    assert [candidate.positive_score for candidate in ranked] == list(map(Decimal, (100, 99, 95)))
    assert [candidate.total_score for candidate in ranked] == list(map(Decimal, (100, 89, 85)))
    assert [candidate.selected_for_plan for candidate in ranked] == [True, False, False]
    assert ranked[0].secondary_alternative is False
    assert ranked[0].primary_symbol is None
    assert all(candidate.secondary_alternative for candidate in ranked[1:])
    assert all(candidate.primary_symbol == "AAA" for candidate in ranked[1:])
    assert all("DUPLICATE_EXPOSURE" in candidate.selection_reasons for candidate in ranked[1:])
    assert [candidate.penalties[2].evidence_ids for candidate in ranked[1:]] == [
        ("ev-aaa-bbb",),
        ("ev-bbb-ccc",),
    ]


def test_correlation_penalties_are_applied_before_final_ordering():
    correlation = CorrelationEvidence(
        left_symbol="AAA",
        right_symbol="BBB",
        coefficient=Decimal("0.95"),
        evidence_ids=("ev-correlation",),
        observed_at=_CUTOFF,
        method_version="fixture-1",
        exposure_description="Shared technology factor exposure",
    )
    ranked = rank_candidates(
        (_candidate("BBB", "0.99"), _candidate("CCC", "0.95"), _candidate("AAA", "1")),
        _regime(),
        RegimePolicy(),
        correlations=(correlation,),
    )
    assert [candidate.symbol for candidate in ranked] == ["AAA", "CCC", "BBB"]
    assert ranked[2].secondary_alternative
    assert ranked[2].primary_symbol == "AAA"
    assert ranked[0].selected_for_plan and ranked[1].selected_for_plan
    assert not ranked[2].selected_for_plan
    assert ranked[2].total_score == Decimal("89")


def test_same_symbol_is_never_selected_twice():
    candidate = _candidate()
    ranked = rank_candidates((candidate, candidate), _regime(), RegimePolicy())
    assert sum(c.selected_for_plan for c in ranked) == 1
    assert ranked[1].secondary_alternative


def test_zero_candidates_is_success():
    assert rank_candidates((), _regime(), RegimePolicy()) == ()


def test_optional_event_warning_is_preserved_and_penalized():
    setup = _setup()
    warning = _block().model_copy(update={"status": GateStatus.WARNING})
    assessment = EventAssessment(plan_status=PlanStatus.REVIEW_REQUIRED, gates=(warning,))
    setup = setup.model_copy(update={"event_assessment": assessment})
    candidate = score_candidate(
        setup,
        **_score_context(setup),
        component_evaluator=_uniform("0.8"),
    )
    assert candidate.plan_status is PlanStatus.REVIEW_REQUIRED
    assert candidate.penalties[1].points == Decimal("5")
    assert candidate.total_score == Decimal("75")
    assert candidate.event_assessment == assessment


def test_default_components_are_calculated_and_no_catalyst_is_not_invented():
    setup = _setup()
    candidate = score_candidate(setup, **_score_context(setup))
    assert candidate.components[-1].quality == Decimal(0)
    assert candidate.components[3].quality == Decimal("0.75")
    assert all(c.calculation and c.metric_ids for c in candidate.components[:-1])
    assert candidate.total_score > 0
    assert type(candidate).model_validate_json(candidate.model_dump_json()) == candidate


def test_reward_risk_quality_uses_the_best_target_scenario():
    candidate = score_candidate(_setup(), **_score_context(_setup()))
    reward_risk = candidate.components[3]
    assert reward_risk.quality == Decimal("0.75")
    assert "best scenario 3" in reward_risk.calculation


def test_nonfinite_or_missing_component_cannot_create_a_score():
    setup = _setup()
    with pytest.raises(ValueError):
        score_candidate(setup, **_score_context(setup), component_evaluator=lambda *args: ())
    with pytest.raises(ValueError):
        _candidate(quality="NaN")


def test_wrong_policy_or_regime_version_and_late_correlations_rejected():
    setup = _setup()
    with pytest.raises(ValueError, match="policy"):
        score_candidate(
            setup,
            **{
                **_score_context(setup),
                "setup_policy": setup.policy.model_copy(update={"version": "2"}),
            },
        )
    with pytest.raises(ValueError, match="policy"):
        rank_candidates((_candidate(),), _regime(), replace(RegimePolicy(), version="other"))
    late = CorrelationEvidence(
        left_symbol="AAA",
        right_symbol="BBB",
        coefficient=Decimal("0.9"),
        evidence_ids=("late",),
        observed_at=_CUTOFF + timedelta(seconds=1),
        method_version="1",
        exposure_description="Shared sector exposure",
    )
    with pytest.raises(ValueError, match="cutoff"):
        rank_candidates(
            (_candidate(), _candidate("BBB")), _regime(), RegimePolicy(), correlations=(late,)
        )


def test_candidate_regime_policy_version_must_match_rank_inputs():
    candidate = _candidate().model_copy(update={"regime_policy_version": "other"})
    with pytest.raises(ValueError, match="regime policy"):
        rank_candidates((candidate,), _regime(), RegimePolicy())


def test_score_candidate_records_the_supplied_regime_policy_version():
    candidate = score_candidate(
        _setup(),
        **{**_score_context(_setup()), "regime_policy_version": "regime-policy-v2"},
    )
    assert candidate.regime_policy_version == "regime-policy-v2"


def test_score_candidate_requires_a_frozen_regime_policy_version():
    setup = _setup()
    context = _score_context(setup)
    del context["regime_policy_version"]

    with pytest.raises(TypeError, match="regime_policy_version"):
        score_candidate(setup, **context)


def test_numeric_results_do_not_depend_on_ambient_decimal_context():
    expected = _candidate()
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        actual = _candidate()
        assert actual == expected
        assert rank_candidates((actual,), _regime(), RegimePolicy())[0].total_score == Decimal("80")


def test_ranking_arithmetic_is_independent_of_ambient_precision_and_traps():
    candidate = _candidate()
    component = candidate.components[0].model_copy(
        update={"quality": Decimal("0.80000000000000000000000000001")}
    )
    candidate = candidate.model_copy(update={"components": (component, *candidate.components[1:])})
    correlation = CorrelationEvidence(
        left_symbol="AAA",
        right_symbol="BBB",
        coefficient=Decimal("0.95000000000000000000000000001"),
        evidence_ids=("ev-context",),
        observed_at=_CUTOFF,
        method_version="fixture-1",
        exposure_description="Context-invariance fixture",
    )
    expected = rank_candidates(
        (candidate, _candidate("BBB")),
        _regime(),
        RegimePolicy(),
        correlations=(correlation,),
    )

    with localcontext() as context:
        context.prec = 6
        context.traps[Inexact] = True
        actual = rank_candidates(
            (candidate, _candidate("BBB")),
            _regime(),
            RegimePolicy(),
            correlations=(correlation,),
        )

    assert actual == expected
