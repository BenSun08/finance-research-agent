from pathlib import Path

import pytest

from finance_research_agent.settings import Settings


def test_settings_reads_process_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ALPACA_API_KEY", "environment-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "environment-secret")

    settings = Settings()

    assert settings.data_dir == tmp_path / "data"
    assert settings.alpaca_api_key.get_secret_value() == "environment-key"
    assert settings.alpaca_api_secret.get_secret_value() == "environment-secret"


def test_settings_prefers_environment_and_serializes_only_secret_free_values(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("ALPACA_API_KEY=file-key\nALPACA_API_SECRET=file-secret\n", encoding="utf-8")
    dotenv.chmod(0o600)

    settings = Settings.from_sources(
        environment={
            "AI_MARKET_RESEARCH_DATA_DIR": str(tmp_path / "data"),
            "ALPACA_API_KEY": "environment-key",
        },
        dotenv_path=dotenv,
        secret_provider=lambda name: "keychain-secret" if name == "ALPACA_API_SECRET" else None,
    )

    assert settings.alpaca_api_key.get_secret_value() == "environment-key"
    assert settings.alpaca_api_secret.get_secret_value() == "keychain-secret"
    assert settings.status() == {
        "alpaca_api_key": "CONFIGURED",
        "alpaca_api_secret": "CONFIGURED",
    }
    serialized = settings.model_dump()
    assert "alpaca_api_key" not in serialized
    assert "alpaca_api_secret" not in serialized
    assert "environment-key" not in repr(settings)
    assert "file-secret" not in repr(settings)


def test_settings_rejects_a_dotenv_file_without_owner_only_permissions(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("ALPACA_API_KEY=file-key\n", encoding="utf-8")
    dotenv.chmod(0o644)

    with pytest.raises(ValueError, match="0600"):
        Settings.from_sources(dotenv_path=dotenv, environment={})
