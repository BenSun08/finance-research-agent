"""Offline release workflow and inherited network-boundary contracts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_uses_locked_dependencies_and_all_supported_interpreters() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    events = workflow.get("on", workflow.get(True))

    assert set(events) == {"pull_request", "push"}
    job = workflow["jobs"]["verify"]
    assert job["env"]["AI_MARKET_RESEARCH_DOTENV_PATH"] == (
        "${{ runner.temp }}/finance-research-agent-config/.env"
    )
    assert any(
        step["name"] == "Prepare isolated configuration root" and "chmod 600" in step["run"]
        for step in job["steps"]
    )
    assert job["strategy"]["matrix"]["python-version"] == ["3.12", "3.13", "3.14"]
    steps = job["steps"]
    install = next(step["run"] for step in steps if "Install locked dependencies" == step["name"])
    assert (
        "pip install --require-hashes --disable-pip-version-check -r requirements.lock" in install
    )
    assert "pip install --disable-pip-version-check --no-deps --no-build-isolation ." in install
    formatting = next(step["run"] for step in steps if step["name"] == "Check formatting")
    assert all(
        f'"{value}"' in formatting
        for value in ("git", "diff", "--name-only", "-z", "HEAD^", "HEAD")
    )
    assert '"--diff-filter=ACMRT"' in formatting
    assert '["ruff", "format", "--check"' in formatting


def test_ci_runs_bounded_doctor_with_private_synthetic_configuration() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify"]["steps"]
    prepare = next(step for step in steps if step["name"] == "Prepare isolated configuration root")
    doctor = next(step for step in steps if step["name"] == "Check local doctor")

    assert '"$AI_MARKET_RESEARCH_DATA_DIR/config"' in prepare["run"]
    assert "config/examples" in prepare["run"]
    assert "chmod 700" in prepare["run"]
    assert "chmod 600" in prepare["run"]
    assert doctor["run"] == "bash scripts/doctor.sh"
    assert "PYTHONDONTWRITEBYTECODE=1" in (ROOT / "scripts/doctor.sh").read_text()
    assert steps.index(doctor) > steps.index(
        next(step for step in steps if step["name"] == "Install locked dependencies")
    )


def test_offline_network_guard_is_inherited_by_child_python_processes() -> None:
    guard = ROOT / "tests" / "network_guard"
    environment = {
        **os.environ,
        "FINANCE_RESEARCH_AGENT_OFFLINE_TESTS": "1",
        "PYTHONPATH": os.pathsep.join((str(guard), str(ROOT / "src"))),
    }
    result = subprocess.run(
        [sys.executable, "-c", "import socket; socket.getaddrinfo('api.example.test', 443)"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0
    assert "offline test network guard" in result.stderr.lower()


def test_offline_network_guard_blocks_direct_external_ip_connections() -> None:
    guard = ROOT / "tests" / "network_guard"
    environment = {
        **os.environ,
        "FINANCE_RESEARCH_AGENT_OFFLINE_TESTS": "1",
        "PYTHONPATH": os.pathsep.join((str(guard), str(ROOT / "src"))),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; socket.create_connection(('203.0.113.8', 443), timeout=0.1)",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0
    assert "offline test network guard" in result.stderr.lower()


def test_offline_network_guard_preserves_unix_socket_process_infrastructure() -> None:
    guard = ROOT / "tests" / "network_guard"
    environment = {
        **os.environ,
        "FINANCE_RESEARCH_AGENT_OFFLINE_TESTS": "1",
        "PYTHONPATH": os.pathsep.join((str(guard), str(ROOT / "src"))),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; left,right=socket.socketpair(); left.sendall(b'ok'); "
            "assert right.recv(2) == b'ok'; left.close(); right.close()",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
