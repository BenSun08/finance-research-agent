from __future__ import annotations

from pathlib import Path
from shutil import copytree

from scripts.check_docs_examples import validate_documentation

ROOT = Path(__file__).resolve().parents[2]


def _copy_docs_root(root: Path) -> Path:
    copytree(ROOT / "skills", root / "skills")
    copytree(ROOT / "docs", root / "docs")
    copytree(ROOT / "src", root / "src")
    (root / "README.md").write_text((ROOT / "README.md").read_text())
    return root


def test_release_readiness_documentation_is_required(tmp_path: Path) -> None:
    root = _copy_docs_root(tmp_path)
    release_readiness = root / "docs/operations/release-readiness.md"
    release_readiness.unlink()

    errors = validate_documentation(root)

    assert any(
        "missing documentation" in error and "release-readiness.md" in error for error in errors
    )


def test_release_readiness_documentation_protects_operational_facts(tmp_path: Path) -> None:
    root = _copy_docs_root(tmp_path)
    release_readiness = root / "docs/operations/release-readiness.md"
    release_readiness.write_text(
        "# Release readiness\n\n"
        "Schedule activation remains PAUSED.\n"
        "The local doctor does not call a provider.\n"
        "Missing exact-head CI evidence is PENDING.\n"
    )

    assert validate_documentation(root) == ()

    release_readiness.write_text(
        release_readiness.read_text().replace("Schedule activation remains PAUSED.", "ACTIVE")
    )

    assert any(
        "release readiness documentation missing PAUSED schedule state" in error
        for error in validate_documentation(root)
    )
