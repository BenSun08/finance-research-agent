"""Offline release workflow and inherited network-boundary contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest
import yaml

import scripts.collect_release_evidence as release_collector
from finance_research_agent.adapters.shadow_evidence import FileSystemReleaseEvidenceReader
from finance_research_agent.evaluation.shadow_records import ReleaseCheckId
from scripts.collect_release_evidence import (
    CI_STEP_NAMES,
    PYTHON_VERSIONS,
    ActionsApiReader,
    CollectionUnavailable,
    ReleaseCollection,
    collect_release_evidence,
)
from scripts.collect_release_evidence import (
    main as collector_main,
)

ROOT = Path(__file__).resolve().parents[2]


def _head_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


EXPECTED_RELEASE_STEPS = {
    ReleaseCheckId.FORMAT: "Check formatting",
    ReleaseCheckId.LINT: "Check Ruff",
    ReleaseCheckId.TYPES: "Check mypy",
    ReleaseCheckId.UNIT_AND_INTEGRATION_TESTS: "Check unit and integration tests",
    ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX: "Check scenarios and failure matrix",
    ReleaseCheckId.BRANCH_COVERAGE: "Check whole-package coverage",
    ReleaseCheckId.SCHEMA_DRIFT: "Check generated schemas",
    ReleaseCheckId.DOCS_AND_EXAMPLES: "Check docs and examples",
    ReleaseCheckId.SECRET_SCAN: "Check tracked secrets",
    ReleaseCheckId.NETWORK_BOUNDARY: "Check offline network boundary",
    ReleaseCheckId.PLUGIN_WHEEL_SDIST: "Check plugin wheel and sdist",
    ReleaseCheckId.FRESH_WHEEL_INSTALL: "Check fresh wheel installation",
    ReleaseCheckId.DOCTOR: "Check local doctor",
    ReleaseCheckId.COMPATIBILITY: "Check compatibility consumers",
}


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


def test_ci_has_one_named_step_for_each_release_check_and_matrix_version() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["verify"]
    step_names = [step["name"] for step in job["steps"]]

    assert set(EXPECTED_RELEASE_STEPS) == set(ReleaseCheckId)
    assert CI_STEP_NAMES == EXPECTED_RELEASE_STEPS
    assert len(set(EXPECTED_RELEASE_STEPS.values())) == len(ReleaseCheckId)
    assert job["strategy"]["matrix"]["python-version"] == ["3.12", "3.13", "3.14"]
    assert {name: step_names.count(name) for name in EXPECTED_RELEASE_STEPS.values()} == {
        name: 1 for name in EXPECTED_RELEASE_STEPS.values()
    }


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


def test_release_evidence_collector_exposes_exact_source_commit_input() -> None:
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            (str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", ""))
        ),
    }
    result = subprocess.run(
        [sys.executable, "-m", "scripts.collect_release_evidence", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert "--source-commit" in result.stdout


def _fake_actions_responses(
    source_commit: str,
    *,
    failed_check: tuple[ReleaseCheckId, str] | None = None,
    missing_check: tuple[ReleaseCheckId, str] | None = None,
) -> dict[str, object]:
    run_id = 91001
    run_route = f"repos/BenSun08/finance-research-agent/actions/runs/{run_id}/jobs"
    workflow_route = "repos/BenSun08/finance-research-agent/actions/workflows/ci.yml/runs"
    jobs = []
    for version_index, python_version in enumerate(("3.12", "3.13", "3.14")):
        steps = []
        for step_index, (check_id, step_name) in enumerate(EXPECTED_RELEASE_STEPS.items()):
            if missing_check == (check_id, python_version):
                continue
            conclusion = "failure" if failed_check == (check_id, python_version) else "success"
            steps.append(
                {
                    "number": step_index + 1,
                    "name": step_name,
                    "status": "completed",
                    "conclusion": conclusion,
                    "completed_at": "2026-10-10T16:30:00Z",
                }
            )
        jobs.append(
            {
                "id": 92000 + version_index,
                "name": f"verify ({python_version})",
                "status": "completed",
                "conclusion": "failure"
                if failed_check and failed_check[1] == python_version
                else "success",
                "steps": steps,
            }
        )
    run = {
        "id": run_id,
        "workflow_id": 93001,
        "run_number": 42,
        "run_attempt": 1,
        "head_sha": source_commit,
        "status": "completed",
        "conclusion": "success",
        "repository": {"id": 94001, "full_name": "BenSun08/finance-research-agent"},
    }
    return {
        workflow_route: [{"workflow_runs": [run]}],
        run_route: [{"jobs": jobs}],
    }


class _FakeActionsApiReader(ActionsApiReader):
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, endpoint: str, query: Mapping[str, str]) -> object:
        self.calls.append((endpoint, dict(query)))
        return self.responses[endpoint]


def test_release_evidence_collection_uses_an_injected_reader_for_exact_head_matrix() -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(source_commit)
    reader = _FakeActionsApiReader(responses)

    collection = collect_release_evidence(source_commit, reader)

    assert collection.status == "PASS"
    assert len(collection.receipts) == len(ReleaseCheckId) * len(PYTHON_VERSIONS)
    assert {item.python_version for item in collection.receipts} == set(PYTHON_VERSIONS)
    assert len({item.id for item in collection.receipts}) == len(collection.receipts)
    assert len(reader.calls) == 2
    assert reader.calls[0][0] == (
        "repos/BenSun08/finance-research-agent/actions/workflows/ci.yml/runs"
    )
    assert reader.calls[0][1] == {"head_sha": source_commit, "per_page": "100"}
    assert reader.calls[1][0] == ("repos/BenSun08/finance-research-agent/actions/runs/91001/jobs")


def test_release_evidence_collection_marks_duplicate_exact_steps_pending() -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(source_commit)
    jobs = responses["repos/BenSun08/finance-research-agent/actions/runs/91001/jobs"][0]["jobs"]
    first_job = jobs[0]
    first_step = first_job["steps"][0]
    first_job["steps"].append(first_step.copy())
    reader = _FakeActionsApiReader(responses)

    collection = collect_release_evidence(source_commit, reader)

    assert collection.status == "PENDING"
    assert (ReleaseCheckId.FORMAT, "3.12") in collection.missing
    assert not any(
        item.check_id is ReleaseCheckId.FORMAT and item.python_version == "3.12"
        for item in collection.receipts
    )


@pytest.mark.parametrize("source_commit", ["short", "0" * 40])
def test_release_evidence_collection_rejects_unavailable_source_identity(
    source_commit: str,
) -> None:
    with pytest.raises(CollectionUnavailable):
        collect_release_evidence(source_commit, _FakeActionsApiReader({}))


def test_release_evidence_collection_keeps_unavailable_actions_pending() -> None:
    class _UnavailableReader:
        def get_json(self, _endpoint: str, _query: Mapping[str, str]) -> object:
            raise CollectionUnavailable("private api error")

    collection = collect_release_evidence("HEAD", _UnavailableReader())

    assert collection.status == "PENDING"
    assert len(collection.missing) == len(ReleaseCheckId) * len(PYTHON_VERSIONS)
    assert collection.pending_reason == "GitHub Actions evidence is unavailable"


def test_release_evidence_collection_keeps_absent_exact_head_run_pending() -> None:
    source_commit = _head_commit()
    reader = _FakeActionsApiReader(
        {
            "repos/BenSun08/finance-research-agent/actions/workflows/ci.yml/runs": [
                {"workflow_runs": []}
            ]
        }
    )

    collection = collect_release_evidence(source_commit, reader)

    assert collection.status == "PENDING"
    assert collection.actions_run_id is None
    assert collection.pending_reason == "no exact-head workflow run"
    assert len(reader.calls) == 1


def test_release_evidence_collection_requires_completed_run_and_unique_matrix_job() -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(source_commit)
    run_page = responses["repos/BenSun08/finance-research-agent/actions/workflows/ci.yml/runs"][0]
    run_page["workflow_runs"][0]["status"] = "in_progress"

    incomplete = collect_release_evidence(source_commit, _FakeActionsApiReader(responses))
    assert incomplete.status == "PENDING"
    assert incomplete.receipts == ()

    responses = _fake_actions_responses(source_commit)
    jobs = responses["repos/BenSun08/finance-research-agent/actions/runs/91001/jobs"][0]["jobs"]
    jobs.append(jobs[0].copy())

    duplicate = collect_release_evidence(source_commit, _FakeActionsApiReader(responses))
    assert duplicate.status == "PENDING"
    assert all((check_id, "3.12") in duplicate.missing for check_id in ReleaseCheckId)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda run, _job, _step: run.update(run_attempt=0),
        lambda _run, job, _step: job.update(id="invalid"),
        lambda _run, _job, step: step.update(status="in_progress"),
        lambda _run, _job, step: step.update(number=0),
        lambda _run, _job, step: step.update(completed_at="2026-10-10T16:30:00"),
        lambda _run, _job, step: step.update(conclusion="unknown"),
    ],
)
def test_release_evidence_collection_keeps_invalid_receipt_fields_pending(mutation) -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(source_commit)
    run = responses["repos/BenSun08/finance-research-agent/actions/workflows/ci.yml/runs"][0][
        "workflow_runs"
    ][0]
    jobs = responses["repos/BenSun08/finance-research-agent/actions/runs/91001/jobs"][0]["jobs"]
    mutation(run, jobs[0], jobs[0]["steps"][0])

    collection = collect_release_evidence(source_commit, _FakeActionsApiReader(responses))

    assert collection.status == "PENDING"
    assert (ReleaseCheckId.FORMAT, "3.12") in collection.missing
    assert not any(
        item.check_id is ReleaseCheckId.FORMAT and item.python_version == "3.12"
        for item in collection.receipts
    )


@pytest.mark.parametrize(
    "jobs_payload",
    [
        None,
        "private error",
        [{"jobs": [None]}],
        [{"jobs": "invalid"}],
    ],
)
def test_release_evidence_collection_keeps_malformed_job_responses_pending(
    jobs_payload: object,
) -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(source_commit)
    responses["repos/BenSun08/finance-research-agent/actions/runs/91001/jobs"] = jobs_payload

    collection = collect_release_evidence(source_commit, _FakeActionsApiReader(responses))

    assert collection.status == "PENDING"
    assert collection.receipts == ()


def test_release_evidence_storage_is_private_canonical_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_commit = _head_commit()
    collection = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: collection)

    assert collector_main(["--source-commit", source_commit]) == 0

    evidence_root = data_root / "diagnostics" / "evaluation"
    record_paths = tuple(evidence_root.glob("release-checks-*.json"))
    assert len(record_paths) == 1
    payload = json.loads(record_paths[0].read_bytes())
    assert payload == [receipt.model_dump(mode="json") for receipt in collection.receipts]
    assert record_paths[0].stat().st_mode & 0o777 == 0o600
    status_paths = tuple(evidence_root.glob("release-status-*.json"))
    assert len(status_paths) == 1
    status_payload = status_paths[0].read_bytes()
    assert json.loads(status_payload) == {
        "actions_run_id": collection.actions_run_id,
        "repository_id": collection.receipts[0].repository_id,
        "run_attempt": collection.run_attempt,
        "schema_version": "0.1",
        "source_commit_sha256": collection.source.commit_sha256,
        "source_tree_sha256": collection.source.tree_sha256,
        "status": "PASS",
        "workflow_id": collection.receipts[0].workflow_id,
    }
    assert status_payload == json.dumps(
        json.loads(status_payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    assert status_paths[0].stat().st_mode & 0o777 == 0o600
    assert (evidence_root / ".release-evidence.lock").stat().st_mode & 0o777 == 0o600
    assert evidence_root.stat().st_mode & 0o777 == 0o700

    assert collector_main(["--source-commit", source_commit]) == 0
    assert len(tuple(evidence_root.glob("release-checks-*.json"))) == 1
    assert len(tuple(evidence_root.glob("release-status-*.json"))) == 1


def test_release_evidence_storage_rejects_permissive_file_from_atomic_write_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    collection = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: collection)

    def permissive_race(source: Path, destination: Path) -> None:
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o644)
        raise FileExistsError

    monkeypatch.setattr(release_collector.os, "link", permissive_race)

    result = collector_main(["--source-commit", source_commit])

    assert result == 0
    assert (
        capsys.readouterr().out
        == "Release evidence: PENDING (private receipt storage is unavailable)\n"
    )
    evidence_files = tuple((data_root / "diagnostics" / "evaluation").glob("release-checks-*.json"))
    assert len(evidence_files) == 1
    assert evidence_files[0].stat().st_mode & 0o777 == 0o644


def test_release_evidence_main_reports_pending_without_leaking_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))

    def unavailable(_source: str, _reader: ActionsApiReader) -> ReleaseCollection:
        raise CollectionUnavailable("private bearer token")

    monkeypatch.setattr(release_collector, "collect_release_evidence", unavailable)

    result = collector_main(["--source-commit", "HEAD"])

    assert result == 0
    assert capsys.readouterr().out == "Release evidence: PENDING (source identity is unavailable)\n"


def test_release_evidence_main_reports_an_invalid_source_as_pending(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = collector_main(["--source-commit", "HEAD~1"])

    assert result == 0
    assert capsys.readouterr().out == "Release evidence: PENDING (source identity is unavailable)\n"


def test_release_evidence_main_uses_read_only_fixed_actions_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(source_commit)
    original_run = subprocess.run
    calls: list[list[str]] = []
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] != "gh":
            return original_run(command, **kwargs)
        calls.append(command)
        endpoint = next(value for value in command if value.startswith("repos/"))
        return subprocess.CompletedProcess(command, 0, json.dumps(responses[endpoint]), "")

    monkeypatch.setattr(release_collector.subprocess, "run", fake_run)

    result = collector_main(["--source-commit", source_commit])

    assert result == 0
    assert capsys.readouterr().out == "Release evidence: PASS (42 exact-head step receipts)\n"
    assert len(calls) == 2
    assert all(
        command[1:6] == ["api", "--method", "GET", "--paginate", "--slurp"] for command in calls
    )
    assert {
        next(value for value in command if value.startswith("repos/")) for command in calls
    } == {
        "repos/BenSun08/finance-research-agent/actions/workflows/ci.yml/runs",
        "repos/BenSun08/finance-research-agent/actions/runs/91001/jobs",
    }


@pytest.mark.parametrize("failure", ["nonzero", "malformed", "missing", "timeout"])
def test_release_evidence_main_sanitizes_actions_cli_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    tmp_path: Path,
) -> None:
    source_commit = _head_commit()
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    original_run = subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] != "gh":
            return original_run(command, **kwargs)
        if failure == "missing":
            raise FileNotFoundError("private environment detail")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("gh", 30, stderr="private token")
        if failure == "malformed":
            return subprocess.CompletedProcess(command, 0, "{malformed", "")
        return subprocess.CompletedProcess(command, 1, "", "private token")

    monkeypatch.setattr(release_collector.subprocess, "run", fake_run)

    result = collector_main(["--source-commit", source_commit])

    assert result == 0
    output = capsys.readouterr().out
    assert output == "Release evidence: PENDING (42 matrix checks missing)\n"
    assert "private" not in output


def test_release_evidence_main_reports_failure_count_and_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    collection = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(
            _fake_actions_responses(source_commit, failed_check=(ReleaseCheckId.LINT, "3.13"))
        ),
    )
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: collection)

    result = collector_main(["--source-commit", source_commit])

    assert result == 1
    assert capsys.readouterr().out == "Release evidence: FAIL (1 unsuccessful exact-head steps)\n"


def test_release_evidence_main_reports_pass_and_pending_collections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    complete = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    incomplete = ReleaseCollection(
        source=complete.source,
        actions_run_id=None,
        run_attempt=None,
        receipts=(),
        missing=((ReleaseCheckId.FORMAT, "3.12"),),
        pending_reason="no exact-head workflow run",
    )
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: complete)

    assert collector_main(["--source-commit", source_commit]) == 0
    assert capsys.readouterr().out == "Release evidence: PASS (42 exact-head step receipts)\n"

    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: incomplete)
    assert collector_main(["--source-commit", source_commit]) == 0
    assert capsys.readouterr().out == "Release evidence: PENDING (1 matrix checks missing)\n"


def test_pending_collection_prevents_reader_from_reusing_an_older_receipt_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    complete = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    receipt = complete.receipts[0]
    evidence_root = tmp_path / "private-data" / "diagnostics" / "evaluation"
    reader = FileSystemReleaseEvidenceReader(
        evidence_root,
        source_commit_sha256=receipt.source_commit_sha256,
        source_tree_sha256=receipt.source_tree_sha256,
        repository_id=receipt.repository_id,
        workflow_id=receipt.workflow_id,
    )
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: complete)

    assert collector_main(["--source-commit", source_commit]) == 0
    assert len(reader.list_receipts()) == len(complete.receipts)
    capsys.readouterr()

    pending = ReleaseCollection(
        source=complete.source,
        actions_run_id=None,
        run_attempt=None,
        receipts=(),
        missing=((ReleaseCheckId.FORMAT, "3.12"),),
        pending_reason="newest exact-head run is incomplete",
    )
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: pending)

    assert collector_main(["--source-commit", source_commit]) == 0
    assert capsys.readouterr().out == "Release evidence: PENDING (1 matrix checks missing)\n"
    assert len(tuple(evidence_root.glob("release-checks-*.json"))) == 1
    assert reader.list_receipts() == ()


def test_watermark_refresh_failure_invalidates_an_older_pass_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    complete = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    receipt = complete.receipts[0]
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    evidence_root = data_root / "diagnostics" / "evaluation"
    reader = FileSystemReleaseEvidenceReader(
        evidence_root,
        source_commit_sha256=receipt.source_commit_sha256,
        source_tree_sha256=receipt.source_tree_sha256,
        repository_id=receipt.repository_id,
        workflow_id=receipt.workflow_id,
    )
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: complete)

    assert collector_main(["--source-commit", source_commit]) == 0
    assert len(reader.list_receipts()) == len(complete.receipts)
    capsys.readouterr()

    pending = ReleaseCollection(
        source=complete.source,
        actions_run_id=None,
        run_attempt=None,
        receipts=(),
        missing=((ReleaseCheckId.FORMAT, "3.12"),),
        pending_reason="newest exact-head run is incomplete",
    )
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: pending)
    original_replace = os.replace

    def fail_watermark_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        if Path(destination).name.startswith("release-status-"):
            raise OSError("private filesystem detail")
        original_replace(source, destination)

    monkeypatch.setattr(release_collector.os, "replace", fail_watermark_replace)

    assert collector_main(["--source-commit", source_commit]) == 0
    assert capsys.readouterr().out == (
        "Release evidence: PENDING (private receipt storage is unavailable)\n"
    )
    assert reader.list_receipts() == ()


def test_overlapping_collections_preserve_the_later_pending_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    complete = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    receipt = complete.receipts[0]
    pending = ReleaseCollection(
        source=complete.source,
        actions_run_id=None,
        run_attempt=None,
        receipts=(),
        missing=((ReleaseCheckId.FORMAT, "3.12"),),
        pending_reason="newest exact-head run is incomplete",
    )
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    evidence_root = data_root / "diagnostics" / "evaluation"
    reader = FileSystemReleaseEvidenceReader(
        evidence_root,
        source_commit_sha256=receipt.source_commit_sha256,
        source_tree_sha256=receipt.source_tree_sha256,
        repository_id=receipt.repository_id,
        workflow_id=receipt.workflow_id,
    )
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    first_collection_started = Event()
    release_first_collection = Event()
    second_invocation_started = Event()
    second_collection_started = Event()
    second_invocation_finished = Event()
    counter_lock = Lock()
    collection_count = 0

    def overlapping_collection(*_args: object) -> ReleaseCollection:
        nonlocal collection_count
        with counter_lock:
            collection_number = collection_count
            collection_count += 1
        if collection_number == 0:
            first_collection_started.set()
            if not release_first_collection.wait(timeout=5):
                raise TimeoutError("test did not release the first collection")
            return complete
        second_collection_started.set()
        return pending

    monkeypatch.setattr(release_collector, "collect_release_evidence", overlapping_collection)

    def run_second_collection() -> int:
        second_invocation_started.set()
        try:
            return collector_main(["--source-commit", source_commit])
        finally:
            second_invocation_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(collector_main, ["--source-commit", source_commit])
        assert first_collection_started.wait(timeout=2)
        second = executor.submit(run_second_collection)
        assert second_invocation_started.wait(timeout=2)
        pending_ran_while_older_collection_was_blocked = second_collection_started.wait(timeout=0.2)
        if pending_ran_while_older_collection_was_blocked:
            assert second_invocation_finished.wait(timeout=2)
        release_first_collection.set()
        assert first.result(timeout=5) == 0
        assert second.result(timeout=5) == 0

    assert not pending_ran_while_older_collection_was_blocked
    assert collection_count == 2
    assert reader.list_receipts() == ()
    capsys.readouterr()


def test_release_evidence_main_rejects_a_symlink_watermark_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    collection = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    diagnostics_root = data_root / "diagnostics"
    diagnostics_root.mkdir(mode=0o700)
    diagnostics_root.chmod(0o700)
    evidence_root = diagnostics_root / "evaluation"
    evidence_root.mkdir(mode=0o700)
    evidence_root.chmod(0o700)
    receipt = collection.receipts[0]
    external = tmp_path / "external-status.json"
    external.write_bytes(b"outside private data root")
    watermark = evidence_root / (
        f"release-status-{receipt.source_commit_sha256}-{receipt.source_tree_sha256}.json"
    )
    watermark.symlink_to(external)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: collection)

    assert collector_main(["--source-commit", source_commit]) == 0

    assert capsys.readouterr().out == (
        "Release evidence: PENDING (private receipt storage is unavailable)\n"
    )
    assert watermark.is_symlink()
    assert external.read_bytes() == b"outside private data root"
    assert tuple(evidence_root.glob("release-checks-*.json")) == ()


def test_release_evidence_main_rejects_a_symlink_collection_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    diagnostics_root = data_root / "diagnostics"
    diagnostics_root.mkdir(mode=0o700)
    diagnostics_root.chmod(0o700)
    evidence_root = diagnostics_root / "evaluation"
    evidence_root.mkdir(mode=0o700)
    evidence_root.chmod(0o700)
    external = tmp_path / "external-lock"
    external.write_bytes(b"outside private data root")
    (evidence_root / ".release-evidence.lock").symlink_to(external)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))

    result = collector_main(["--source-commit", source_commit])

    assert result == 0
    assert capsys.readouterr().out == (
        "Release evidence: PENDING (private receipt storage is unavailable)\n"
    )
    assert external.read_bytes() == b"outside private data root"
    assert tuple(evidence_root.glob("release-status-*.json")) == ()


def test_release_evidence_main_rejects_a_hard_linked_collection_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    diagnostics_root = data_root / "diagnostics"
    diagnostics_root.mkdir(mode=0o700)
    diagnostics_root.chmod(0o700)
    evidence_root = diagnostics_root / "evaluation"
    evidence_root.mkdir(mode=0o700)
    evidence_root.chmod(0o700)
    external = tmp_path / "external-lock"
    external.write_bytes(b"outside private data root")
    (evidence_root / ".release-evidence.lock").hardlink_to(external)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))

    result = collector_main(["--source-commit", source_commit])

    assert result == 0
    assert capsys.readouterr().out == (
        "Release evidence: PENDING (private receipt storage is unavailable)\n"
    )
    assert external.read_bytes() == b"outside private data root"
    assert external.stat().st_nlink == 2
    assert tuple(evidence_root.glob("release-status-*.json")) == ()


def test_release_evidence_main_reports_storage_failure_as_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_commit = _head_commit()
    collection = collect_release_evidence(
        source_commit,
        _FakeActionsApiReader(_fake_actions_responses(source_commit)),
    )
    data_root = tmp_path / "public-data"
    data_root.mkdir(mode=0o755)
    data_root.chmod(0o755)
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(data_root))
    monkeypatch.setenv("AI_MARKET_RESEARCH_DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(release_collector, "collect_release_evidence", lambda *_args: collection)

    result = collector_main(["--source-commit", source_commit])

    assert result == 0
    assert (
        capsys.readouterr().out
        == "Release evidence: PENDING (private receipt storage is unavailable)\n"
    )


def _run_release_collector(
    tmp_path: Path,
    responses: dict[str, object],
    *,
    source_commit: str,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(mode=0o700)
    data_root = tmp_path / "private-data"
    data_root.mkdir(mode=0o700)
    response_file = tmp_path / "actions-responses.json"
    response_file.write_text(json.dumps(responses), encoding="utf-8")
    calls_file = tmp_path / "gh-calls.json"
    calls_file.write_text("[]", encoding="utf-8")
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "arguments = sys.argv[1:]\n"
        "route = next((value for value in arguments if value.startswith('repos/')), None)\n"
        "calls_path = Path(os.environ['RELEASE_TEST_GH_CALLS'])\n"
        "calls = json.loads(calls_path.read_text(encoding='utf-8'))\n"
        "calls.append(arguments)\n"
        "calls_path.write_text(json.dumps(calls), encoding='utf-8')\n"
        "if route is None or arguments[:4] != ['api', '--method', 'GET', '--paginate']:\n"
        "    raise SystemExit(2)\n"
        "responses = json.loads(Path(os.environ['RELEASE_TEST_GH_RESPONSES']).read_text("
        "encoding='utf-8'))\n"
        "if route not in responses:\n"
        "    print('release-test-secret-token', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "print(json.dumps(responses[route]))\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o700)
    guard = ROOT / "tests/network_guard"
    environment = {
        **os.environ,
        "AI_MARKET_RESEARCH_DATA_DIR": str(data_root),
        "AI_MARKET_RESEARCH_DOTENV_PATH": str(tmp_path / ".env"),
        "FINANCE_RESEARCH_AGENT_OFFLINE_TESTS": "1",
        "PATH": os.pathsep.join((str(fake_bin), os.environ["PATH"])),
        "PYTHONPATH": os.pathsep.join((str(guard), str(ROOT / "src"), str(ROOT))),
        "RELEASE_TEST_GH_CALLS": str(calls_file),
        "RELEASE_TEST_GH_RESPONSES": str(response_file),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.collect_release_evidence",
            "--source-commit",
            source_commit,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    return result, data_root, calls_file


def test_release_evidence_collector_writes_all_exact_head_step_receipts(
    tmp_path: Path,
) -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(source_commit)

    result, data_root, calls_file = _run_release_collector(
        tmp_path, responses, source_commit=source_commit
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Release evidence: PASS" in result.stdout
    assert "release-test-secret-token" not in result.stdout + result.stderr
    evidence_root = data_root / "diagnostics" / "evaluation"
    record_paths = tuple(evidence_root.glob("release-checks-*.json"))
    assert len(record_paths) == 1
    payload = json.loads(record_paths[0].read_text(encoding="utf-8"))
    assert len(payload) == len(ReleaseCheckId) * 3
    assert {receipt["check_id"] for receipt in payload} == {item.value for item in ReleaseCheckId}
    assert {receipt["python_version"] for receipt in payload} == {"3.12", "3.13", "3.14"}
    assert {receipt["conclusion"] for receipt in payload} == {"success"}
    assert (evidence_root.stat().st_mode & 0o777) == 0o700
    assert len(json.loads(calls_file.read_text(encoding="utf-8"))) == 2


def test_release_evidence_collector_fails_only_for_an_exact_head_failed_step(
    tmp_path: Path,
) -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(
        source_commit,
        failed_check=(ReleaseCheckId.LINT, "3.13"),
    )

    result, data_root, _ = _run_release_collector(tmp_path, responses, source_commit=source_commit)

    assert result.returncode != 0
    assert "Release evidence: FAIL" in result.stdout
    payloads = tuple((data_root / "diagnostics" / "evaluation").glob("release-checks-*.json"))
    assert len(payloads) == 1
    payload = json.loads(payloads[0].read_text(encoding="utf-8"))
    assert sum(receipt["conclusion"] == "failure" for receipt in payload) == 1


def test_release_evidence_collector_keeps_missing_exact_checks_pending(
    tmp_path: Path,
) -> None:
    source_commit = _head_commit()
    responses = _fake_actions_responses(
        source_commit,
        missing_check=(ReleaseCheckId.TYPES, "3.14"),
    )

    result, data_root, _ = _run_release_collector(tmp_path, responses, source_commit=source_commit)

    assert result.returncode == 0
    assert "Release evidence: PENDING" in result.stdout
    payloads = tuple((data_root / "diagnostics" / "evaluation").glob("release-checks-*.json"))
    assert len(payloads) == 1
    payload = json.loads(payloads[0].read_text(encoding="utf-8"))
    assert len(payload) == len(ReleaseCheckId) * 3 - 1
    assert not any(
        receipt["check_id"] == ReleaseCheckId.TYPES.value and receipt["python_version"] == "3.14"
        for receipt in payload
    )


def test_release_evidence_collector_keeps_missing_github_evidence_pending_and_secret_free(
    tmp_path: Path,
) -> None:
    source_commit = _head_commit()

    result, data_root, _ = _run_release_collector(tmp_path, {}, source_commit=source_commit)

    assert result.returncode == 0
    assert "Release evidence: PENDING" in result.stdout
    assert "release-test-secret-token" not in result.stdout + result.stderr
    assert not tuple((data_root / "diagnostics" / "evaluation").glob("release-checks-*.json"))
