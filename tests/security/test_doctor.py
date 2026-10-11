from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import doctor

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config/examples"


def _doctor_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    data_root = tmp_path / "private-data"
    config_root = data_root / "config"
    config_root.mkdir(parents=True, mode=0o700)
    for source in EXAMPLE_CONFIG.glob("*.yaml"):
        target = config_root / source.name
        shutil.copyfile(source, target)
        target.chmod(0o600)
    data_root.chmod(0o700)

    dotenv = tmp_path / "doctor.env"
    dotenv.touch(mode=0o600)
    return (
        {
            **os.environ,
            "AI_MARKET_RESEARCH_DATA_DIR": str(data_root),
            "AI_MARKET_RESEARCH_DOTENV_PATH": str(dotenv),
            "ALPACA_API_KEY": "doctor-sentinel-key",
            "ALPACA_API_SECRET": "doctor-sentinel-secret",
            "FINANCE_RESEARCH_AGENT_OFFLINE_TESTS": "1",
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.environ["PATH"])),
            "PYTHONPATH": os.pathsep.join(
                (
                    str(ROOT / "tests/network_guard"),
                    str(ROOT / "src"),
                    str(ROOT),
                )
            ),
        },
        data_root,
    )


def _run_doctor(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts/doctor.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _write_fake_interpreter(path: Path, *, supported: bool) -> None:
    version_status = 0 if supported else 1
    path.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f"  *sys.version_info*) exit {version_status} ;;\n"
        f"  \"-m scripts.doctor\") printf 'selected-interpreter: {path.name}\\n'; exit 0 ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def test_doctor_passes_offline_checks_without_exposing_credential_values(tmp_path: Path) -> None:
    environment, data_root = _doctor_environment(tmp_path)

    result = _run_doctor(environment)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Alpaca API key: configured" in output
    assert "Alpaca API secret: configured" in output
    assert "MCP initialize/list-tools: PASS" in output
    assert "doctor-sentinel-key" not in output
    assert "doctor-sentinel-secret" not in output
    assert not tuple(data_root.glob(".doctor-write.*"))


def test_doctor_fails_closed_on_invalid_config_without_echoing_input(tmp_path: Path) -> None:
    environment, data_root = _doctor_environment(tmp_path)
    (data_root / "config/source-policy.yaml").write_text(
        "secret-looking-doctor-input: doctor-sentinel-secret\n", encoding="utf-8"
    )
    (data_root / "config/source-policy.yaml").chmod(0o600)

    result = _run_doctor(environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Configuration and host policy: FAIL" in output
    assert "doctor-sentinel-secret" not in output


def test_doctor_rejects_nonprivate_data_root_and_removes_probe(tmp_path: Path) -> None:
    environment, data_root = _doctor_environment(tmp_path)
    data_root.chmod(0o755)

    result = _run_doctor(environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Private data-root permissions: FAIL" in output
    assert not tuple(data_root.glob(".doctor-write.*"))


def test_doctor_probes_data_root_configured_in_dotenv(tmp_path: Path) -> None:
    environment, data_root = _doctor_environment(tmp_path)
    dotenv = Path(environment["AI_MARKET_RESEARCH_DOTENV_PATH"])
    environment.pop("AI_MARKET_RESEARCH_DATA_DIR")
    dotenv.write_text(f"AI_MARKET_RESEARCH_DATA_DIR={data_root}\n", encoding="utf-8")
    dotenv.chmod(0o600)

    result = _run_doctor(environment)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Data-root writable probe: PASS" in output
    assert not tuple(data_root.glob(".doctor-write.*"))


def test_doctor_skips_unsupported_python_when_supported_python3_is_available(
    tmp_path: Path,
) -> None:
    environment, _ = _doctor_environment(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(mode=0o700)
    _write_fake_interpreter(fake_bin / "python", supported=False)
    _write_fake_interpreter(fake_bin / "python3", supported=True)
    environment["PATH"] = os.pathsep.join((str(fake_bin), environment["PATH"]))

    result = _run_doctor(environment)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "selected-interpreter: python3" in result.stdout
    assert "selected-interpreter: python\n" not in result.stdout


def test_doctor_reports_unsupported_python_without_import_traceback(tmp_path: Path) -> None:
    environment, _ = _doctor_environment(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(mode=0o700)
    _write_fake_interpreter(fake_bin / "python", supported=False)
    _write_fake_interpreter(fake_bin / "python3", supported=False)
    environment["PATH"] = os.pathsep.join((str(fake_bin), environment["PATH"]))

    result = _run_doctor(environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Supported Python: FAIL" in output
    assert "Traceback" not in output


def test_doctor_entrypoint_keeps_missing_credentials_outside_offline_readiness(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    environment, data_root = _doctor_environment(tmp_path)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv(
        "AI_MARKET_RESEARCH_DOTENV_PATH", environment["AI_MARKET_RESEARCH_DOTENV_PATH"]
    )
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_API_SECRET", "")
    subchecks: list[list[str]] = []

    def record_subcheck(command, root, *, timeout):
        subchecks.append(command)
        return True

    monkeypatch.setattr(doctor, "_run_check", record_subcheck)

    result = doctor.main()

    output = capsys.readouterr().out
    assert result == 0
    assert "Alpaca API key: missing" in output
    assert "Alpaca API secret: missing" in output
    assert "Offline package readiness: READY" in output
    assert all("no:cacheprovider" in command for command in subchecks)


def test_project_identity_reads_exact_locked_package_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "finance-research-agent"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "finance-research-agent"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor, "version", lambda name: "1.2.3")

    assert doctor._project_versions(tmp_path) == ("1.2.3", "1.2.3", "1.2.3")


def test_project_identity_rejects_missing_locked_package(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "finance-research-agent"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="package records"):
        doctor._project_versions(tmp_path)


def test_project_identity_rejects_duplicate_locked_package(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "finance-research-agent"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    package = '[[package]]\nname = "finance-research-agent"\nversion = "1.2.3"\n'
    (tmp_path / "uv.lock").write_text(f"version = 1\n{package}{package}", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly once"):
        doctor._project_versions(tmp_path)


def test_private_tree_rejects_open_root_child_and_config_file_modes(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    assert doctor._private_tree(data_root)

    data_root.chmod(0o755)
    assert not doctor._private_tree(data_root)
    data_root.chmod(0o700)

    config_root = data_root / "config"
    config_root.mkdir(mode=0o755)
    assert not doctor._private_tree(data_root)
    config_root.chmod(0o700)

    secret_config = config_root / "private.yaml"
    secret_config.write_text("version: '1'\n", encoding="utf-8")
    secret_config.chmod(0o644)
    assert not doctor._private_tree(data_root)
    secret_config.chmod(0o600)
    assert doctor._private_tree(data_root)

    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    (data_root / "logs").symlink_to(external, target_is_directory=True)
    assert not doctor._private_tree(data_root)


def test_private_tree_rejects_open_nested_evaluation_directory(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    evaluation_root = data_root / "diagnostics" / "evaluation"
    evaluation_root.mkdir(parents=True, mode=0o700)
    data_root.chmod(0o700)
    (data_root / "diagnostics").chmod(0o700)
    evaluation_root.chmod(0o755)

    assert not doctor._private_tree(data_root)


def test_doctor_subchecks_force_offline_child_environment(tmp_path: Path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    def run(command, *, cwd, env, capture_output, check, timeout, text):
        observed.update(command=command, cwd=cwd, env=env, timeout=timeout)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(doctor.subprocess, "run", run)

    assert doctor._run_check(["python", "-m", "pytest"], tmp_path, timeout=4)
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["FINANCE_RESEARCH_AGENT_OFFLINE_TESTS"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert str(tmp_path / "tests/network_guard") in environment["PYTHONPATH"]
    assert observed["timeout"] == 4


@pytest.mark.parametrize("error", (OSError("unavailable"), subprocess.TimeoutExpired("pytest", 1)))
def test_doctor_subchecks_fail_closed_when_they_cannot_run(
    tmp_path: Path,
    monkeypatch,
    error: Exception,
) -> None:
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(doctor.subprocess, "run", fail)

    assert not doctor._run_check(["python", "-m", "pytest"], tmp_path, timeout=1)


def test_doctor_entrypoint_hides_environment_and_contract_errors(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    environment, data_root = _doctor_environment(tmp_path)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv(
        "AI_MARKET_RESEARCH_DOTENV_PATH", environment["AI_MARKET_RESEARCH_DOTENV_PATH"]
    )

    def fail(*args, **kwargs):
        raise ValueError("doctor-hidden-sentinel")

    def fail_filesystem(*args, **kwargs):
        raise PermissionError("doctor-hidden-sentinel")

    import finance_research_agent.schema_export as schema_export
    import finance_research_agent.settings as settings_module
    import scripts.check_docs_examples as docs_check

    monkeypatch.setattr(doctor, "_project_versions", fail)
    monkeypatch.setattr(doctor, "_private_tree", fail_filesystem)
    monkeypatch.setattr(settings_module.Settings, "from_sources", classmethod(fail))
    monkeypatch.setattr(schema_export, "check_schemas", fail)
    monkeypatch.setattr(docs_check, "validate_product_a_contracts", fail)
    monkeypatch.setattr(doctor, "_run_check", lambda command, root, *, timeout: True)

    result = doctor.main()

    output = capsys.readouterr().out
    assert result != 0
    assert "Installed locked package: FAIL" in output
    assert "Alpaca API key: unavailable" in output
    assert "Alpaca API secret: unavailable" in output
    assert "Configuration and host policy: FAIL" in output
    assert "Generated schemas: FAIL" in output
    assert "Documentation and skill contracts: FAIL" in output
    assert "doctor-hidden-sentinel" not in output
