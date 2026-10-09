"""Explicit trusted component versions for offline source-level service tests."""

from finance_research_agent.application.component_versions import component_versions_for_skill
from finance_research_agent.domain.models import ComponentVersions, ConfigurationSnapshot

SYNTHETIC_SKILL_VERSION = "sha256:" + "a" * 64


def synthetic_component_versions(snapshot: ConfigurationSnapshot) -> ComponentVersions:
    return component_versions_for_skill(snapshot, SYNTHETIC_SKILL_VERSION)
