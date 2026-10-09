"""The approved personal local plugin has only fixed root components."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_manifest_preserves_approved_identity_and_root_resource_paths():
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_bytes())
    assert set(manifest) == {"name", "version", "description", "skills", "mcpServers", "interface"}
    assert manifest["name"] == "ai-market-research-agent"
    assert manifest["version"] == "0.1.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["displayName"] == "AI Market Research Agent"


def test_bundled_mcp_is_one_wrapped_stdio_server_without_environment():
    config = json.loads((ROOT / ".mcp.json").read_bytes())
    assert config == {
        "mcpServers": {
            "ai-market-research": {"command": "ai-market-research-mcp", "args": []},
        }
    }


def test_personal_marketplace_points_only_to_the_local_plugin_root():
    catalog = json.loads((ROOT / ".agents/plugins/marketplace.json").read_bytes())
    assert catalog == {
        "name": "personal-finance-research",
        "interface": {"displayName": "Personal Finance Research"},
        "plugins": [
            {
                "name": "ai-market-research-agent",
                "source": {"source": "local", "path": "./"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }
        ],
    }
