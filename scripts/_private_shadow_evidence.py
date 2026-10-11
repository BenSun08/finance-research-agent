"""Shared strict input and private storage helpers for bounded evidence CLIs."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

from pydantic import BaseModel

from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.settings import Settings

_MAX_REQUEST_BYTES = 1_000_000
_EVIDENCE_DIRECTORIES = frozenset({"shadow-observations", "operator-attestations"})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_json_constant(_value: str) -> None:
    raise ValueError("non-JSON numeric constant")


def read_strict_request[RequestT: BaseModel](model: type[RequestT]) -> RequestT:
    """Read one bounded strict JSON object without duplicate keys from stdin."""
    payload = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if len(payload) > _MAX_REQUEST_BYTES:
        raise ValueError("evidence request exceeds the input limit")
    parsed = json.loads(
        payload,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_non_json_constant,
    )
    if not isinstance(parsed, dict):
        raise ValueError("evidence request must be one JSON object")
    return model.model_validate_json(payload, strict=True)


def _ensure_private_directory(path: Path, *, create: bool) -> Path:
    if path.is_symlink():
        raise ValueError("private evidence directory must not be a symlink")
    if not path.exists():
        if not create:
            raise ValueError("configured private data root must already exist")
        path.mkdir(mode=0o700)
    if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("private evidence directories must have mode 0700 or stricter")
    return path


def private_evidence_roots(directory_name: str) -> tuple[Path, Path]:
    """Return the configured private data root and its dedicated evidence store."""
    if directory_name not in _EVIDENCE_DIRECTORIES:
        raise ValueError("evidence directory is not allowed")
    settings = Settings.from_sources(secret_provider=lambda _name: None)
    data_root = _ensure_private_directory(settings.data_dir.expanduser().absolute(), create=False)
    diagnostics_root = _ensure_private_directory(data_root / "diagnostics", create=True)
    evaluation_root = _ensure_private_directory(diagnostics_root / "evaluation", create=True)
    record_root = _ensure_private_directory(evaluation_root / directory_name, create=True)
    return data_root, record_root


def write_receipt(receipt: BaseModel) -> None:
    sys.stdout.buffer.write(canonical_bytes(receipt) + b"\n")
