"""Tracked-only secret scanning and one-time bootstrap baseline safety."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import check_secrets


@pytest.mark.parametrize(
    ("bootstrap", "arguments"),
    (
        (False, ["ls-files", "-z"]),
        (True, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]),
    ),
)
def test_git_path_modes_use_exact_nul_delimited_file_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap: bool,
    arguments: list[str],
) -> None:
    git = check_secrets.shutil.which("git")
    assert git is not None
    calls: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append({"args": args, **kwargs})
        return SimpleNamespace(returncode=0, stdout=b"one.py\0nested/two.py\0", stderr=b"")

    monkeypatch.setattr(check_secrets.subprocess, "run", fake_run)

    paths = check_secrets._git_file_list(tmp_path, bootstrap=bootstrap)

    assert paths == ("one.py", "nested/two.py")
    assert calls[0]["args"] == [str(Path(git).resolve()), *arguments]
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["timeout"] <= 10
    assert calls[0]["check"] is True


def test_candidate_validation_rejects_symlinks_and_paths_outside_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("# synthetic fixture\n", encoding="utf-8")
    link = root / "linked.py"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        check_secrets._regular_repo_files(root, ("linked.py",))
    with pytest.raises(ValueError, match="outside repository"):
        check_secrets._regular_repo_files(root, ("../outside.py",))


def test_new_secret_output_names_file_and_plugin_without_printing_candidate_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    source = root / "tests" / "synthetic_fixture.py"
    source.parent.mkdir(parents=True)
    fake_value = "AKIA" + "IOSFODNN7EXAMPLE"
    source.write_text(f"token = {fake_value!r}\n", encoding="utf-8")
    baseline_path = root / ".secrets.baseline"
    baseline_path.write_text(
        '{"version":"1.5.0","plugins_used":[],"filters_used":[],"results":{}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: (".secrets.baseline",) + ("tests/synthetic_fixture.py",),
    )

    result = check_secrets.main([], root=root)
    output = capsys.readouterr().out

    assert result == 1
    assert "tests/synthetic_fixture.py" in output
    assert "AWS Access Key" in output
    assert fake_value not in output


def test_bootstrap_excludes_only_its_output_and_writes_an_atomic_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_text("answer = 42\n", encoding="utf-8")
    (root / "untracked.py").write_text("message = 'ordinary fixture'\n", encoding="utf-8")
    (root / ".secrets.baseline").write_text("previous contents\n", encoding="utf-8")
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: (".secrets.baseline", "tracked.py", "untracked.py"),
    )

    result = check_secrets.main(["--write-baseline"], root=root)

    assert result == 0
    assert "baseline" in capsys.readouterr().out.lower()
    baseline = json.loads((root / ".secrets.baseline").read_text(encoding="utf-8"))
    assert baseline["version"] == "1.5.0"
    assert ".secrets.baseline" not in baseline["results"]
    assert not tuple(root.glob(".secrets.baseline.*.tmp"))


def test_baseline_writer_stores_repository_relative_filenames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    candidate = root / "tests" / "candidate.py"
    candidate.parent.mkdir()
    baseline_path = root / ".secrets.baseline"
    document = {
        "version": "1.5.0",
        "plugins_used": [],
        "filters_used": [],
        "results": {str(candidate): [{"filename": str(candidate), "type": "Example Plugin"}]},
    }
    monkeypatch.setattr(check_secrets.baseline, "format_for_output", lambda _: document)

    check_secrets._write_baseline(baseline_path, object())  # type: ignore[arg-type]

    written = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert written["results"] == {
        "tests/candidate.py": [{"filename": "tests/candidate.py", "type": "Example Plugin"}]
    }


def test_secret_collection_paths_are_repository_relative(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    candidate = root / "tests" / "synthetic.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text('token = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")

    with check_secrets.default_settings():
        collection = check_secrets._scan_files(root.resolve(), (candidate,))

    filenames = {secret.filename for _, secret in collection}
    assert filenames == {"tests/synthetic.py"}
    assert all(secret in collection[filename] for filename, secret in collection)


def test_normal_scan_detects_secret_values_embedded_in_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    baseline_path = root / ".secrets.baseline"
    fake_value = "AKIA" + "IOSFODNN7EXAMPLE"
    baseline_path.write_text(
        json.dumps(
            {
                "version": "1.5.0",
                "plugins_used": [],
                "filters_used": [],
                "results": {},
                "review_note": fake_value,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: (".secrets.baseline",),
    )

    result = check_secrets.main([], root=root)
    output = capsys.readouterr().out

    assert result == 1
    assert ".secrets.baseline" in output
    assert fake_value not in output


def test_normal_scan_masks_only_detect_secrets_fingerprint_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".secrets.baseline").write_text(
        json.dumps(
            {
                "version": "1.5.0",
                "plugins_used": [],
                "filters_used": [],
                "results": {
                    "src/fixture.py": [
                        {
                            "filename": "src/fixture.py",
                            "hashed_secret": "a" * 40,
                            "is_secret": False,
                            "is_verified": False,
                            "line_number": 1,
                            "type": "Example Plugin",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: (".secrets.baseline",),
    )

    result = check_secrets.main([], root=root)
    output = capsys.readouterr()

    assert result == 0
    assert "no new potential secrets" in output.out


def test_baseline_filter_configuration_cannot_hide_a_tracked_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "src" / "candidate.py"
    source.parent.mkdir()
    fake_value = "AKIA" + "IOSFODNN7EXAMPLE"
    source.write_text(f"credential = {fake_value!r}\n", encoding="utf-8")
    with check_secrets.default_settings():
        document = check_secrets.baseline.format_for_output(check_secrets.SecretsCollection())
    document["results"] = {}
    document["filters_used"].append(
        {
            "path": "detect_secrets.filters.regex.should_exclude_line",
            "pattern": [".*"],
        }
    )
    (root / ".secrets.baseline").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: (".secrets.baseline", "src/candidate.py"),
    )

    result = check_secrets.main([], root=root)
    output = capsys.readouterr().out

    assert result == 1
    assert "src/candidate.py" in output
    assert fake_value not in output


def test_normal_scan_fails_closed_when_baseline_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(check_secrets, "_git_file_list", lambda root, *, bootstrap: ())

    result = check_secrets.main([], root=root)

    assert result == 2
    assert "baseline is missing or unsafe" in capsys.readouterr().err


def test_normal_scan_rejects_an_untracked_baseline_that_suppresses_a_tracked_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    source = root / "src" / "candidate.py"
    source.parent.mkdir(parents=True)
    source.write_text('credential = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    with check_secrets.default_settings():
        collection = check_secrets._scan_files(root.resolve(), (source,))
    for _, secret in collection:
        secret.is_secret = False
    check_secrets._write_baseline(root / ".secrets.baseline", collection)
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: ("src/candidate.py",),
    )

    result = check_secrets.main([], root=root)
    output = capsys.readouterr()

    assert result == 2
    assert "secret baseline is not tracked" in output.err
    assert "no new potential secrets" not in output.out


def test_normal_scan_fails_closed_on_symlinked_or_malformed_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside-baseline"
    outside.write_text("{}\n", encoding="utf-8")
    baseline_path = root / ".secrets.baseline"
    baseline_path.symlink_to(outside)
    monkeypatch.setattr(check_secrets, "_git_file_list", lambda root, *, bootstrap: ())

    assert check_secrets.main([], root=root) == 2
    assert "baseline is missing or unsafe" in capsys.readouterr().err

    baseline_path.unlink()
    baseline_path.write_text("not json\n", encoding="utf-8")
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: (".secrets.baseline",),
    )
    assert check_secrets.main([], root=root) == 2
    error = capsys.readouterr().err
    assert "baseline could not be loaded (UnableToReadBaselineError)" in error
    assert "not json" not in error


def test_git_discovery_failure_is_reported_without_sensitive_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(check_secrets.shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError, match="trusted Git executable"):
        check_secrets._git_file_list(tmp_path, bootstrap=False)

    root = tmp_path / "repo"
    root.mkdir()
    assert check_secrets.main([], root=root) == 2
    assert "RuntimeError" in capsys.readouterr().err


def test_bootstrap_write_failure_is_reported_without_leaving_a_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(check_secrets, "_git_file_list", lambda root, *, bootstrap: ())

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("private filesystem detail")

    monkeypatch.setattr(os, "replace", fail_replace)

    assert check_secrets.main(["--write-baseline"], root=root) == 2
    error = capsys.readouterr().err
    assert "baseline could not be written (OSError)" in error
    assert "private filesystem detail" not in error
    assert not tuple(root.glob(".secrets.baseline.*.tmp"))


def test_invalid_candidate_paths_and_git_subprocess_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    directory = root / "not-a-file"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        check_secrets._regular_repo_files(root, ("not-a-file",))

    with pytest.raises(ValueError, match="missing or unreadable"):
        check_secrets._regular_repo_files(root, ("missing.py",))

    def fail_git(_: Path, *, bootstrap: bool) -> tuple[str, ...]:
        raise subprocess.TimeoutExpired("git", 10)

    monkeypatch.setattr(check_secrets, "_git_file_list", fail_git)
    assert check_secrets.main([], root=root) == 2
    assert "TimeoutExpired" in capsys.readouterr().err


@pytest.mark.parametrize("audit_result", (None, True, False))
def test_normal_scan_requires_every_baseline_finding_to_be_audited_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    audit_result: bool | None,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    fake_value = "AKIA" + "IOSFODNN7EXAMPLE"
    (root / "fixture.py").write_text(f"token = {fake_value!r}\n", encoding="utf-8")
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: ("fixture.py", ".secrets.baseline"),
    )
    assert check_secrets.main(["--write-baseline"], root=root) == 0
    capsys.readouterr()

    baseline_path = root / ".secrets.baseline"
    document = json.loads(baseline_path.read_text(encoding="utf-8"))
    for findings in document["results"].values():
        for finding in findings:
            finding["is_secret"] = audit_result
    baseline_path.write_text(json.dumps(document), encoding="utf-8")

    result = check_secrets.main([], root=root)
    output = capsys.readouterr()

    assert fake_value not in output.out + output.err
    if audit_result is False:
        assert result == 0
        assert "no new potential secrets" in output.out
    else:
        assert result == 2
        assert "baseline must mark every finding as a reviewed false positive" in output.err
        assert "fixture.py: AWS Access Key" in output.err


def test_normal_scan_checks_baseline_content_without_printing_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    baseline_path = root / ".secrets.baseline"
    baseline_path.write_text(
        json.dumps(
            {
                "version": "1.5.0",
                "plugins_used": [],
                "filters_used": [],
                "results": {},
                "scanner_fixture": "AKIAIOSFODNN7EXAMPLE",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check_secrets,
        "_git_file_list",
        lambda root, *, bootstrap: (".secrets.baseline", "tracked.py"),
    )

    assert check_secrets.main([], root=root) == 1
    output = capsys.readouterr()

    assert ".secrets.baseline" in output.out
    assert "AKIAIOSFODNN7EXAMPLE" not in output.out + output.err
