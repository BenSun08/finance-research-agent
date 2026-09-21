"""Secret-free process settings and credential lookup helpers."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class SecretProvider(Protocol):
    """Read one named secret without exposing a secret collection."""

    def get(self, name: str) -> str | None: ...


class MacOSKeychainSecretProvider:
    """Read credentials from macOS Keychain using the system helper."""

    def __init__(self, *, service: str = "finance-research-agent") -> None:
        self._service = service

    def get(self, name: str) -> str | None:
        if sys.platform != "darwin":
            return None
        result = subprocess.run(
            ["security", "find-generic-password", "-s", self._service, "-a", name, "-w"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.rstrip("\r\n")
        return value or None


class _CallableSecretProvider:
    def __init__(self, callback: object) -> None:
        self._callback = callback

    def get(self, name: str) -> str | None:
        callback = self._callback
        if not callable(callback):
            raise TypeError("secret_provider must implement get or be callable")
        value = callback(name)
        if value is not None and type(value) is not str:
            raise TypeError("secret providers must return strings or None")
        return value


class Settings(BaseModel):
    """Validated settings whose public serialization never contains credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    data_dir: Path = Field(default=Path("data"), validation_alias="AI_MARKET_RESEARCH_DATA_DIR")
    alpaca_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    alpaca_api_secret: SecretStr | None = Field(default=None, exclude=True, repr=False)

    def __init__(self, **data: object) -> None:
        if not data:
            loaded = type(self).from_sources()
            data = {
                "data_dir": loaded.data_dir,
                "alpaca_api_key": loaded.alpaca_api_key,
                "alpaca_api_secret": loaded.alpaca_api_secret,
            }
        super().__init__(**data)

    @classmethod
    def from_sources(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        secret_provider: SecretProvider | Callable[[str], str | None] | None = None,
        dotenv_path: Path | None = None,
    ) -> Settings:
        env = dict(os.environ if environment is None else environment)
        path = Path(".env") if dotenv_path is None else dotenv_path
        dotenv = _read_dotenv(path) if path.exists() else {}
        provider = secret_provider if secret_provider is not None else MacOSKeychainSecretProvider()
        provider_adapter: SecretProvider = (
            cast(SecretProvider, provider)
            if hasattr(provider, "get")
            else _CallableSecretProvider(provider)
        )

        def lookup(name: str) -> str | None:
            if name in env:
                return env[name] or None
            value = provider_adapter.get(name)
            if value:
                return value
            return dotenv.get(name) or None

        return cls.model_validate(
            {
                "data_dir": env.get("AI_MARKET_RESEARCH_DATA_DIR", "data"),
                "alpaca_api_key": lookup("ALPACA_API_KEY"),
                "alpaca_api_secret": lookup("ALPACA_API_SECRET"),
            }
        )

    def status(self) -> dict[str, str]:
        return {
            "alpaca_api_key": "CONFIGURED" if self.alpaca_api_key is not None else "MISSING",
            "alpaca_api_secret": "CONFIGURED" if self.alpaca_api_secret is not None else "MISSING",
        }


def _read_dotenv(path: Path) -> dict[str, str]:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise ValueError(".env must have mode 0600")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if separator != "=" or name not in {"ALPACA_API_KEY", "ALPACA_API_SECRET"}:
            raise ValueError(f"invalid .env entry at line {line_number}")
        values[name] = value.strip().strip("\"'")
    return values
