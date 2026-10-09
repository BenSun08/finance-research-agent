"""Offline source, sdist, wheel and installed-resource safety checks."""

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from finance_research_agent.application.skill_bundle import (
    PLUGIN_RESOURCE_PATHS,
    PLUGIN_RESOURCE_ROOT,
    compute_skill_version,
)

ROOT = Path(__file__).resolve().parents[2]
BUILD_PYTHON = os.environ.get("FINANCE_BUILD_PYTHON", sys.executable)


def _source_copy(destination):
    destination.mkdir()
    for name in ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE"):
        shutil.copyfile(ROOT / name, destination / name)
    for name in ("src", "prompts", "skills", ".codex-plugin"):
        shutil.copytree(
            ROOT / name,
            destination / name,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
    shutil.copyfile(ROOT / ".mcp.json", destination / ".mcp.json")
    return destination


def _build(source, output, kind="wheel", *, expect_success=True):
    output.mkdir(parents=True, exist_ok=True)
    code = f"import setuptools.build_meta as b; print(b.build_{kind}({str(output)!r}))"
    result = subprocess.run(
        [BUILD_PYTHON, "-c", code],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if expect_success:
        assert result.returncode == 0, result.stdout + result.stderr
        return next(output.glob("*.whl" if kind == "wheel" else "*.tar.gz"))
    assert result.returncode != 0, "unsafe resource build unexpectedly succeeded"
    assert (
        "canonical" in result.stdout + result.stderr or "symlink" in result.stdout + result.stderr
    )
    return result


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("product-a-package")
    source = _source_copy(root / "source")
    wheel = _build(source, root / "wheel")
    sdist = _build(source, root / "sdist", "sdist")
    extracted = root / "sdist-source"
    extracted.mkdir()
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        archive.extractall(extracted, filter="data")
    sdist_source = next(extracted.iterdir())
    rebuilt_wheel = _build(sdist_source, root / "rebuilt-wheel")
    return root, source, wheel, sdist, names, rebuilt_wheel


def _wheel_resources(wheel):
    with ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.startswith(PLUGIN_RESOURCE_ROOT + "/")]
        assert sorted(names) == sorted(
            PLUGIN_RESOURCE_ROOT + "/" + name for name in PLUGIN_RESOURCE_PATHS
        )
        assert len(names) == len(set(names))
        return {
            name: archive.read(PLUGIN_RESOURCE_ROOT + "/" + name) for name in PLUGIN_RESOURCE_PATHS
        }


def test_wheel_contains_only_fixed_plugin_resources_with_exact_canonical_bytes(artifacts):
    _, source, wheel, _, _, rebuilt = artifacts
    resources = _wheel_resources(wheel)
    assert resources == {name: (source / name).read_bytes() for name in PLUGIN_RESOURCE_PATHS}
    assert _wheel_resources(rebuilt) == resources
    with ZipFile(wheel) as archive:
        assert (
            archive.read("finance_research_agent/data/research-brief-draft.md")
            == (source / "prompts/research-brief-draft.md").read_bytes()
        )


def test_sdist_contains_canonical_sources_once_and_no_competing_editable_copy(artifacts):
    _, _, _, _, names, _ = artifacts
    stripped = [name.split("/", 1)[1] for name in names if "/" in name]
    for name in PLUGIN_RESOURCE_PATHS:
        assert stripped.count(name) == 1
    assert not any(PLUGIN_RESOURCE_ROOT in name for name in stripped)


def _installed_versions(target, cwd):
    code = (
        "import json,sys; from pathlib import Path; "
        "sys.path.insert(0,sys.argv[1]); "
        "from finance_research_agent.application.skill_bundle "
        "import load_installed_skill_versions; "
        "import finance_research_agent; "
        "assert Path(finance_research_agent.__file__).is_relative_to(Path(sys.argv[1])); "
        "print(json.dumps(load_installed_skill_versions()))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(target)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


def test_temporary_wheel_install_reads_installed_bytes_without_source_fallback(artifacts):
    root, source, wheel, _, _, _ = artifacts
    target = root / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-compile",
            "--target",
            str(target),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    expected = compute_skill_version(
        {
            "SKILL.md": (source / "skills/premarket-research/SKILL.md").read_bytes(),
            "references/workflow-contract.yaml": (
                source / "skills/premarket-research/references/workflow-contract.yaml"
            ).read_bytes(),
        }
    )
    first = _installed_versions(target, root)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == [expected, "0.1.0"]
    source_skill = source / "skills/premarket-research/SKILL.md"
    source_skill.write_bytes(source_skill.read_bytes() + b"source changed\n")
    assert json.loads(_installed_versions(target, root).stdout) == [expected, "0.1.0"]
    installed_skill = target / PLUGIN_RESOURCE_ROOT / "skills/premarket-research/SKILL.md"
    installed_skill.write_bytes(installed_skill.read_bytes() + b"installed changed\n")
    assert json.loads(_installed_versions(target, root).stdout)[0] != expected
    installed_skill.unlink()
    assert _installed_versions(target, source).returncode != 0


@pytest.mark.parametrize("kind", ("wheel", "sdist"))
@pytest.mark.parametrize(
    "unsafe",
    (
        "leaf",
        "ancestor",
        "skill_root",
        "source_copy",
        "stale_build",
        "build_root",
        "generated_symlink",
    ),
)
def test_build_rejects_symlinks_competing_source_and_stale_generated_resources(
    tmp_path, kind, unsafe
):
    source = _source_copy(tmp_path / "source")
    if unsafe in {"leaf", "ancestor", "skill_root"}:
        path = {
            "leaf": source / "skills/premarket-research/SKILL.md",
            "ancestor": source / "skills/premarket-research/references",
            "skill_root": source / "skills",
        }[unsafe]
        outside = tmp_path / "outside"
        path.rename(outside)
        path.symlink_to(outside, target_is_directory=outside.is_dir())
    elif unsafe == "source_copy":
        path = source / "src" / PLUGIN_RESOURCE_ROOT / "skills/premarket-research/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"competing editable copy")
    elif unsafe in {"build_root", "generated_symlink"}:
        path = source / ("build" if unsafe == "build_root" else "build/lib/finance_research_agent")
        outside = tmp_path / "outside"
        outside.mkdir()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(outside, target_is_directory=True)
    else:
        path = source / "build/lib" / PLUGIN_RESOURCE_ROOT / "unexpected.txt"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"stale generated resource")
    _build(source, tmp_path / "output", kind, expect_success=False)
