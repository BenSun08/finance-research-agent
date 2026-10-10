"""Product A scenario evaluation contracts."""

from finance_research_agent.evaluation.citation_sampling import (
    select_citation_entailment_sample,
)
from finance_research_agent.evaluation.harness import (
    EvaluationHarness,
    execute_evaluation_scenario,
)
from finance_research_agent.evaluation.models import (
    CurrentScopeExpectation,
    DomainAssertion,
    DomainAssertionKind,
    EvaluationOutcome,
    EvaluationScenario,
    FailureInjectionId,
    FixtureSetId,
    ScenarioAssertionId,
    ScenarioId,
    ScenarioOutcomeExpectation,
)
from finance_research_agent.evaluation.scenarios import DEFAULT_MANIFEST, load_evaluation_scenarios

__all__ = [
    "CurrentScopeExpectation",
    "DEFAULT_MANIFEST",
    "DomainAssertion",
    "DomainAssertionKind",
    "EvaluationScenario",
    "EvaluationOutcome",
    "EvaluationHarness",
    "FailureInjectionId",
    "FixtureSetId",
    "ScenarioAssertionId",
    "ScenarioId",
    "ScenarioOutcomeExpectation",
    "select_citation_entailment_sample",
    "load_evaluation_scenarios",
    "execute_evaluation_scenario",
]
