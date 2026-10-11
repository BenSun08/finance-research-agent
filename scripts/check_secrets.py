"""Scan tracked files and fail only for potential secrets absent from the baseline."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from detect_secrets import SecretsCollection
from detect_secrets.core import baseline
from detect_secrets.settings import default_settings

_BASELINE_NAME = ".secrets.baseline"
_BASELINE_HASH = re.compile(r'("hashed_secret"\s*:\s*")[0-9a-f]{40}(")')


def _git_file_list(root: Path, *, bootstrap: bool) -> tuple[str, ...]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("trusted Git executable was not found")
    command = [str(Path(git).resolve(strict=True)), "ls-files"]
    if bootstrap:
        command.extend(("--cached", "--others", "--exclude-standard"))
    command.append("-z")
    result = subprocess.run(  # noqa: S603 -- resolved trusted Git binary
        command,
        cwd=root,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return tuple(os.fsdecode(path) for path in result.stdout.split(b"\0") if path)


def _regular_repo_files(root: Path, relative_paths: Sequence[str]) -> tuple[Path, ...]:
    if root.is_symlink():
        raise ValueError("repository root is a symlink")
    resolved_root = root.resolve(strict=True)
    files: list[Path] = []
    for raw_path in relative_paths:
        relative_path = PurePosixPath(raw_path)
        if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
            raise ValueError("path is outside repository")
        candidate = resolved_root.joinpath(*relative_path.parts)
        if any(
            parent.is_symlink()
            for parent in (candidate, *candidate.parents)
            if parent != resolved_root
        ):
            raise ValueError("symlinked input path is forbidden")
        try:
            resolved = candidate.resolve(strict=True)
            mode = candidate.stat().st_mode
        except OSError as exc:
            raise ValueError("missing or unreadable repository input") from exc
        if not resolved.is_relative_to(resolved_root):
            raise ValueError("path is outside repository")
        if not stat.S_ISREG(mode):
            raise ValueError("repository input must be a regular file")
        files.append(candidate)
    return tuple(files)


def _scan_files(root: Path, files: Sequence[Path]) -> SecretsCollection:
    collection = SecretsCollection(root=str(root))
    for path in files:
        collection.scan_file(path.relative_to(root).as_posix())
    normalized = SecretsCollection(root=str(root))
    for filename, secret in collection:
        secret.filename = filename
        normalized[filename].add(secret)
    return normalized


def _write_baseline(path: Path, collection: SecretsCollection) -> None:
    document = baseline.format_for_output(collection)
    repository_root = path.parent.resolve(strict=True)
    results = document.get("results")
    if not isinstance(results, dict):
        raise ValueError("secret baseline results must be a mapping")
    relative_results: dict[str, object] = {}
    for filename, findings in results.items():
        relative = _repository_relative_path(filename, repository_root)
        if not isinstance(findings, list):
            raise ValueError("secret baseline findings must be a list")
        relative_findings: list[object] = []
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("secret baseline finding must be a mapping")
            normalized = dict(finding)
            if "filename" in normalized:
                normalized["filename"] = _repository_relative_path(
                    normalized["filename"], repository_root
                ).as_posix()
            relative_findings.append(normalized)
        relative_results[relative.as_posix()] = relative_findings
    document["results"] = relative_results
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _repository_relative_path(filename: object, root: Path) -> Path:
    if not isinstance(filename, str):
        raise ValueError("secret baseline filenames must be strings")
    candidate = Path(filename)
    if candidate.is_absolute():
        try:
            return candidate.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ValueError("secret baseline path is outside repository") from exc
    if ".." in candidate.parts:
        raise ValueError("secret baseline path is outside repository")
    return candidate


def _new_findings(collection: SecretsCollection) -> tuple[tuple[str, str], ...]:
    return tuple(sorted({(filename, secret.type) for filename, secret in collection}))


def _scan_baseline(path: Path) -> SecretsCollection:
    """Scan baseline content while masking only its known non-secret fingerprints."""
    content = path.read_text(encoding="utf-8")
    sanitized = _BASELINE_HASH.sub(r"\1<redacted-baseline-fingerprint>\2", content)
    with tempfile.TemporaryDirectory(prefix="finance-research-agent-secret-scan-") as directory:
        temporary_root = Path(directory)
        temporary_path = temporary_root / _BASELINE_NAME
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(sanitized)
            return _scan_files(temporary_root, (temporary_path,))
        finally:
            temporary_path.unlink(missing_ok=True)


def _merge_collections(target: SecretsCollection, source: SecretsCollection) -> None:
    for filename, secret in source:
        target[filename].add(secret)


def _scan_repository(root: Path, *, bootstrap: bool) -> int:
    baseline_path = root / _BASELINE_NAME
    relative_paths = _git_file_list(root, bootstrap=bootstrap)
    # Bootstrap excludes its output. Normal scans include the tracked baseline,
    # with known SHA-1 fingerprints masked by _scan_baseline.
    if bootstrap:
        relative_paths = tuple(path for path in relative_paths if path != _BASELINE_NAME)
    candidate_files = _regular_repo_files(root, relative_paths)
    files = tuple(path for path in candidate_files if path != baseline_path)

    with default_settings():
        existing: SecretsCollection | None = None
        baseline_is_tracked = _BASELINE_NAME in relative_paths
        if not bootstrap:
            if not baseline_path.is_file() or baseline_path.is_symlink():
                print(
                    "secret baseline is missing or unsafe; audit before bootstrap", file=sys.stderr
                )
                return 2
        collection = _scan_files(root, files)
        if not bootstrap and baseline_is_tracked:
            try:
                _merge_collections(collection, _scan_baseline(baseline_path))
            except (OSError, UnicodeError) as exc:
                print(
                    f"secret baseline could not be scanned ({type(exc).__name__})",
                    file=sys.stderr,
                )
                return 2

        if bootstrap:
            try:
                _write_baseline(baseline_path, collection)
            except OSError as exc:
                print(
                    f"secret baseline could not be written ({type(exc).__name__})", file=sys.stderr
                )
                return 2
            print(f"wrote {baseline_path.relative_to(root)}; audit it before normal scans")
            return 0

        # Baseline plugin/filter configuration is untrusted scanner input.
        # Load it only after every current candidate has been scanned using
        # the default scanner configuration.
        try:
            existing = baseline.load(
                baseline.load_from_file(str(baseline_path)), filename=str(baseline_path)
            )
        except Exception as exc:
            print(f"secret baseline could not be loaded ({type(exc).__name__})", file=sys.stderr)
            return 2

        assert existing is not None
        unaudited = tuple(
            sorted(
                (filename, secret.type)
                for filename, secret in existing
                if secret.is_secret is not False
            )
        )
        if unaudited:
            print(
                "secret baseline must mark every finding as a reviewed false positive",
                file=sys.stderr,
            )
            for filename, plugin in unaudited:
                print(f"{filename}: {plugin}", file=sys.stderr)
            return 2
        unexpected = collection - existing

    findings = _new_findings(unexpected)
    if findings:
        print("new potential secrets:")
        for filename, plugin in findings:
            print(f"{filename}: {plugin}")
        return 1
    print("no new potential secrets")
    return 0


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="bootstrap a baseline from cached and untracked non-ignored files for manual audit",
    )
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1] if root is None else root
    try:
        return _scan_repository(repository_root.resolve(strict=True), bootstrap=args.write_baseline)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"secret scan could not complete ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
