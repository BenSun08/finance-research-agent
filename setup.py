"""Build installed resources from their sole canonical repository sources."""

import runpy
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

_ROOT = Path(__file__).parent
_BUNDLE = runpy.run_path(str(_ROOT / "src/finance_research_agent/application/skill_bundle.py"))
_PLUGIN_RESOURCE = Path(_BUNDLE["PLUGIN_RESOURCE_ROOT"])
_PLUGIN_PATHS = _BUNDLE["PLUGIN_RESOURCE_PATHS"]
_PROMPT_PATH = "prompts/research-brief-draft.md"
_PROMPT_RESOURCE = Path("finance_research_agent/data/research-brief-draft.md")


def _source_file(raw_path: str) -> Path:
    _BUNDLE["validate_logical_path"](raw_path)
    path = _ROOT
    if path.is_symlink():
        raise ValueError("canonical source root contains a symlink")
    for part in raw_path.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("canonical source contains a symlink")
    if not path.is_file() or not path.resolve().is_relative_to(_ROOT.resolve()):
        raise ValueError("canonical source must be a confined regular file")
    return path


def _validate_generated(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("generated canonical resource root contains a symlink")
    if not path.exists():
        return
    if not path.is_dir():
        raise ValueError("generated canonical resource root is not a directory")
    expected = set(_PLUGIN_PATHS)
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise ValueError("generated canonical resource contains a symlink")
        if entry.is_file() and entry.relative_to(path).as_posix() not in expected:
            raise ValueError("unexpected stale canonical generated resource")


def _validate_sources() -> None:
    competing = _safe_destination(str(_ROOT / "src"), _PLUGIN_RESOURCE)
    if competing.exists() or competing.is_symlink():
        raise ValueError("competing canonical resource copy under src is prohibited")
    _source_file(_PROMPT_PATH)
    for name in _PLUGIN_PATHS:
        _source_file(name)
    generated = _safe_destination(str(_ROOT / "build/lib"), _PLUGIN_RESOURCE)
    _validate_generated(generated)


def _safe_destination(build_lib: str, relative: Path) -> Path:
    base = Path(build_lib)
    path = base
    if any(parent.is_symlink() for parent in (base, *base.parents)):
        raise ValueError("canonical build root contains a symlink")
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("canonical build resource contains a symlink")
    return path


class BuildPyWithCanonicalResources(build_py):
    """Generate packaged resources without keeping another editable source."""

    def run(self) -> None:
        _validate_sources()
        _safe_destination(self.build_lib, _PLUGIN_RESOURCE)
        _validate_generated(Path(self.build_lib) / _PLUGIN_RESOURCE)
        super().run()
        for source, relative in (
            (_PROMPT_PATH, _PROMPT_RESOURCE),
            *((name, _PLUGIN_RESOURCE / name) for name in _PLUGIN_PATHS),
        ):
            destination = _safe_destination(self.build_lib, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_source_file(source).read_bytes())

    def get_outputs(self, include_bytecode: int = 1) -> list[str]:
        outputs = super().get_outputs(include_bytecode)
        resources = [_PROMPT_RESOURCE, *(_PLUGIN_RESOURCE / name for name in _PLUGIN_PATHS)]
        return [*outputs, *(str(Path(self.build_lib) / path) for path in resources)]


class SdistWithCanonicalResources(sdist):
    """Reject unsafe canonical sources before sdist can dereference them."""

    def run(self) -> None:
        _validate_sources()
        super().run()


setup(cmdclass={"build_py": BuildPyWithCanonicalResources, "sdist": SdistWithCanonicalResources})
