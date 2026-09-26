"""Include the canonical prompt as package data without maintaining a copy."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_PROMPT_SOURCE = Path(__file__).parent / "prompts" / "research-brief-draft.md"
_PROMPT_RESOURCE = Path("finance_research_agent") / "data" / "research-brief-draft.md"


class BuildPyWithCanonicalPrompt(build_py):
    """Copy the root prompt into built packages from its sole source file."""

    def run(self) -> None:
        super().run()
        destination = Path(self.build_lib) / _PROMPT_RESOURCE
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.copy_file(str(_PROMPT_SOURCE), str(destination))

    def get_outputs(self, include_bytecode: int = 1) -> list[str]:
        outputs = super().get_outputs(include_bytecode)
        return [*outputs, str(Path(self.build_lib) / _PROMPT_RESOURCE)]


setup(cmdclass={"build_py": BuildPyWithCanonicalPrompt})
