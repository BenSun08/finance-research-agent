from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import import_module
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from finance_research_agent.application.shadow_evidence_service import (
    RecordOperatorReleaseAttestation,
)
from scripts import _private_shadow_evidence as private_evidence_cli

ROOT = Path(__file__).resolve().parents[2]


def _run_cli(
    tmp_path: Path,
    module: str,
    request_json: str,
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    data_root.chmod(0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "AI_MARKET_RESEARCH_DATA_DIR": str(data_root),
            "AI_MARKET_RESEARCH_DOTENV_PATH": str(tmp_path / "missing.env"),
            "ALPACA_API_KEY": "",
            "ALPACA_API_SECRET": "",
            "FINANCE_RESEARCH_AGENT_OFFLINE_TESTS": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(ROOT / "tests/network_guard"), str(ROOT / "src"), str(ROOT))
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        env=environment,
        input=request_json,
        capture_output=True,
        check=False,
        timeout=15,
        text=True,
    )
    return result, data_root


def _run_main_in_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: str,
    request_json: str,
) -> tuple[int, Path, bytes, str]:
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    data_root.chmod(0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_API_SECRET", "")
    input_stream = SimpleNamespace(buffer=BytesIO(request_json.encode("utf-8")))
    output_stream = SimpleNamespace(buffer=BytesIO())
    error_stream = StringIO()
    monkeypatch.setattr(sys, "stdin", input_stream)
    monkeypatch.setattr(sys, "stdout", output_stream)
    monkeypatch.setattr(sys, "stderr", error_stream)
    cli = import_module(module)

    result = cli.main([])

    return result, data_root, output_stream.buffer.getvalue(), error_stream.getvalue()


def test_shadow_observation_cli_writes_private_service_stamped_record(tmp_path: Path) -> None:
    request = json.dumps(
        {
            "id": "shadow-day-cli-1",
            "market_date": "2026-10-09",
            "session_id": "session-cli-1",
            "availability": "ONLINE",
        }
    )

    result, data_root = _run_cli(
        tmp_path,
        "scripts.record_shadow_observation",
        request,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["observation_id"] == "shadow-day-cli-1"
    assert receipt["recorded_at"].endswith("Z")
    evaluation_root = data_root / "diagnostics" / "evaluation"
    observation_root = evaluation_root / "shadow-observations"
    record_path = observation_root / "shadow-day-cli-1.json"
    record = json.loads(record_path.read_text())
    assert record["recorded_at"] == receipt["recorded_at"]
    assert record_path.stat().st_mode & 0o777 == 0o600
    assert evaluation_root.stat().st_mode & 0o777 == 0o700
    assert observation_root.stat().st_mode & 0o777 == 0o700
    assert not (data_root / "runs").exists()
    assert "provider" not in result.stdout.lower()


def test_operator_attestation_cli_writes_private_append_only_record(tmp_path: Path) -> None:
    request = json.dumps(
        {
            "id": "attestation-cli-1",
            "check_id": "DESKTOP_INSTALL",
            "result": "PASS",
            "operator_id": "operator-cli",
            "package_version": "0.5.0.dev0",
            "skill_version": "sha256:" + "b" * 64,
            "source_commit_sha256": "c" * 64,
            "source_tree_sha256": "d" * 64,
            "evidence_sha256": "e" * 64,
        }
    )

    result, data_root = _run_cli(
        tmp_path,
        "scripts.record_operator_release_attestation",
        request,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["attestation_id"] == "attestation-cli-1"
    attestation_root = data_root / "diagnostics" / "evaluation" / "operator-attestations"
    record_path = attestation_root / "attestation-cli-1.json"
    record = json.loads(record_path.read_text())
    assert record["recorded_at"] == receipt["recorded_at"]
    assert record_path.stat().st_mode & 0o777 == 0o600
    assert attestation_root.stat().st_mode & 0o777 == 0o700
    assert "provider" not in result.stdout.lower()


@pytest.mark.parametrize(
    "module",
    (
        "scripts.record_shadow_observation",
        "scripts.record_operator_release_attestation",
    ),
)
def test_shadow_evidence_clis_reject_duplicate_keys_without_writing(
    tmp_path: Path,
    module: str,
) -> None:
    result, data_root = _run_cli(tmp_path, module, '{"id":"first","id":"second"}')

    assert result.returncode == 1
    assert result.stdout == ""
    assert "first" not in result.stderr
    assert not (data_root / "diagnostics").exists()


@pytest.mark.parametrize(
    "module",
    (
        "scripts.record_shadow_observation",
        "scripts.record_operator_release_attestation",
    ),
)
def test_shadow_evidence_clis_do_not_accept_a_data_root_override(
    tmp_path: Path,
    module: str,
) -> None:
    external = tmp_path / "external"
    result, data_root = _run_cli(
        tmp_path,
        module,
        "{}",
        "--data-root",
        str(external),
    )

    assert result.returncode == 2
    assert str(external) in result.stderr
    assert not external.exists()
    assert not (data_root / "diagnostics").exists()


@pytest.mark.parametrize(
    ("module", "request_json", "record_name"),
    (
        (
            "scripts.record_shadow_observation",
            '{"id":"shadow-day-direct-1","market_date":"2026-10-09",'
            '"session_id":"session-direct-1","availability":"ONLINE"}',
            "shadow-observations/shadow-day-direct-1.json",
        ),
        (
            "scripts.record_operator_release_attestation",
            '{"id":"attestation-direct-1","check_id":"DESKTOP_INSTALL",'
            '"result":"PASS","operator_id":"operator-direct",'
            '"package_version":"0.5.0.dev0","skill_version":"sha256:'
            + "b" * 64
            + '","source_commit_sha256":"'
            + "c" * 64
            + '","source_tree_sha256":"'
            + "d" * 64
            + '","evidence_sha256":"'
            + "e" * 64
            + '"}',
            "operator-attestations/attestation-direct-1.json",
        ),
    ),
)
def test_shadow_evidence_cli_main_records_a_receipt_in_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: str,
    request_json: str,
    record_name: str,
) -> None:
    result, data_root, output, errors = _run_main_in_process(
        monkeypatch,
        tmp_path,
        module,
        request_json,
    )

    assert result == 0
    assert errors == ""
    receipt = json.loads(output)
    assert receipt["recorded_at"].endswith("Z")
    assert (data_root / "diagnostics" / "evaluation" / record_name).is_file()


@pytest.mark.parametrize(
    "module",
    (
        "scripts.record_shadow_observation",
        "scripts.record_operator_release_attestation",
    ),
)
def test_shadow_evidence_cli_main_rejects_duplicate_keys_without_leaking_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: str,
) -> None:
    result, data_root, output, errors = _run_main_in_process(
        monkeypatch,
        tmp_path,
        module,
        '{"id":"private-id","id":"duplicate"}',
    )

    assert result == 1
    assert output == b""
    assert "private-id" not in errors
    assert not (data_root / "diagnostics").exists()


@pytest.mark.parametrize(
    ("payload", "error_type"),
    (
        (b" " * 1_000_001, ValueError),
        (b"[]", ValueError),
        (b'{"id":NaN}', ValueError),
        (b'{"id":"one","id":"two"}', ValueError),
        (b"{}", ValidationError),
    ),
)
def test_strict_stdin_parser_rejects_unbounded_or_non_object_requests(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    error_type: type[Exception],
) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(payload)))

    with pytest.raises(error_type):
        private_evidence_cli.read_strict_request(RecordOperatorReleaseAttestation)


def test_private_evidence_roots_reject_unapproved_directory_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not allowed"):
        private_evidence_cli.private_evidence_roots("outside")


def test_private_evidence_roots_require_an_existing_private_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "missing-data"
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))

    with pytest.raises(ValueError, match="already exist"):
        private_evidence_cli.private_evidence_roots("operator-attestations")

    assert not data_root.exists()


def test_private_evidence_roots_reject_symlinked_or_permissive_subdirectories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    data_root.chmod(0o700)
    external = tmp_path / "external-diagnostics"
    external.mkdir(mode=0o700)
    external.chmod(0o700)
    (data_root / "diagnostics").symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))

    with pytest.raises(ValueError, match="symlink"):
        private_evidence_cli.private_evidence_roots("shadow-observations")

    (data_root / "diagnostics").unlink()
    evaluation = data_root / "diagnostics" / "evaluation"
    evaluation.mkdir(mode=0o700, parents=True)
    evaluation.chmod(0o755)
    with pytest.raises(ValueError, match="mode 0700"):
        private_evidence_cli.private_evidence_roots("shadow-observations")
