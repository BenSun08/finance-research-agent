"""Trusted version assembly keeps source tests and installed discovery separate."""

import pytest

from finance_research_agent.application import component_versions
from finance_research_agent.application.component_versions import (
    component_versions_for_skill,
    current_component_versions,
)
from finance_research_agent.domain.models import ConfigurationSnapshot
from finance_research_agent.domain.types import FrozenMap


@pytest.fixture
def snapshot():
    return ConfigurationSnapshot(
        content_hash_sha256="a" * 64,
        file_hashes=FrozenMap({"watchlist.yaml": "b" * 64}),
        watchlist_version="1",
        regime_policy_version="1",
        setup_policy_version="1",
        risk_policy_version="1",
        source_policy_version="1",
    )


def test_pure_trusted_assembly_does_not_discover_installed_resources(snapshot, monkeypatch):
    def forbidden():
        raise AssertionError("explicit trusted assembly must not inspect installed files")

    monkeypatch.setattr(component_versions, "load_installed_skill_versions", forbidden)
    result = component_versions_for_skill(snapshot, "sha256:" + "a" * 64)
    assert result.skill_version == "sha256:" + "a" * 64
    assert result.plugin_version == "0.1.0"
    assert result.watchlist_version == snapshot.watchlist_version


def test_current_versions_use_installed_skill_and_manifest_version(snapshot, monkeypatch):
    calls = []

    def installed():
        calls.append("installed")
        return "sha256:" + "b" * 64, "0.1.0"

    monkeypatch.setattr(component_versions, "load_installed_skill_versions", installed)
    result = current_component_versions(snapshot)
    assert result.skill_version == "sha256:" + "b" * 64
    assert result.plugin_version == "0.1.0"
    assert calls == ["installed"]


def test_current_versions_fail_closed_when_installation_is_unavailable(snapshot, monkeypatch):
    def missing():
        raise RuntimeError("installed plugin resources are missing")

    monkeypatch.setattr(component_versions, "load_installed_skill_versions", missing)
    with pytest.raises(RuntimeError, match="installed plugin"):
        current_component_versions(snapshot)
