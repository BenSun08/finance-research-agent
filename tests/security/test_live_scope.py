import shlex
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_default_pytest_options_exclude_live_marker() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = shlex.split(pyproject["tool"]["pytest"]["ini_options"]["addopts"])

    marker_position = addopts.index("-m")
    assert addopts[marker_position + 1] == "not live"


def test_ci_keeps_the_unqualified_pytest_command() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "        run: pytest\n" in workflow
    assert 'run: pytest -m "not live"' not in workflow
