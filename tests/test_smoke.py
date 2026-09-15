import tomllib
from importlib import import_module
from pathlib import Path

from finance_research_agent import __version__


def test_finance_research_agent_is_importable() -> None:
    module = import_module("finance_research_agent")

    assert module.__name__ == "finance_research_agent"


def test_package_version_matches_project_metadata() -> None:
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project_file.open("rb") as stream:
        project = tomllib.load(stream)

    assert __version__ == project["project"]["version"]
