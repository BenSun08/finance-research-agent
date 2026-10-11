from __future__ import annotations

from pathlib import Path
from shutil import copytree

from scripts.check_docs_examples import validate_documentation, validate_product_a_contracts

ROOT = Path(__file__).resolve().parents[2]


def _copy_docs_root(root: Path) -> Path:
    copytree(ROOT / "skills", root / "skills")
    copytree(ROOT / "docs", root / "docs")
    copytree(ROOT / "src", root / "src")
    copytree(ROOT / "config", root / "config")
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


def test_product_a_example_validation_rejects_unreviewed_symbols_and_personal_rationales(
    tmp_path: Path,
) -> None:
    root = _copy_docs_root(tmp_path)
    watchlist = root / "config/examples/watchlist.yaml"
    watchlist.write_text(
        watchlist.read_text(encoding="utf-8")
        .replace("symbol: AAPL", "symbol: TSLA")
        .replace(
            "research_rationale: Synthetic example for offline configuration validation.",
            "research_rationale: Personal account example for Ben Smith's retirement portfolio.",
        ),
        encoding="utf-8",
    )

    errors = validate_product_a_contracts(root)

    assert any("example symbol" in error and "de-identified" in error for error in errors)
    assert any("example rationale" in error and "de-identified" in error for error in errors)


def test_product_a_example_validation_rejects_personal_notes(tmp_path: Path) -> None:
    root = _copy_docs_root(tmp_path)
    watchlist = root / "config/examples/watchlist.yaml"
    watchlist.write_text(
        watchlist.read_text(encoding="utf-8").replace(
            "notes: Synthetic data only.",
            "notes: Ben Smith's personal retirement account.",
        ),
        encoding="utf-8",
    )

    errors = validate_product_a_contracts(root)

    assert any("example notes" in error and "de-identified" in error for error in errors)
