"""Validate the bounded Product A skill contracts without executing workflows."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from finance_research_agent.application.config_service import (
    FIXED_CONFIGURATION_FILES,
    load_configuration,
)
from finance_research_agent.application.operations import OPERATION_NAMES
from finance_research_agent.application.skill_bundle import declared_skill_resources
from finance_research_agent.application.skill_contracts import (
    REQUIRED_SKILL_SECTIONS as REQUIRED_SKILL_SECTIONS,
)
from finance_research_agent.application.skill_contracts import (
    parse_skill_bytes,
    validate_workflow_bytes,
)

_REVIEWED_EXAMPLE_SYMBOLS = frozenset(
    {
        "AAPL",
        "DIA",
        "GLD",
        "HYG",
        "IWM",
        "LQD",
        "QQQ",
        "SPY",
        "TLT",
        "UUP",
        "USO",
        "VIX",
        "XLB",
        "XLC",
        "XLE",
        "XLF",
        "XLI",
        "XLK",
        "XLP",
        "XLRE",
        "XLU",
        "XLV",
        "XLY",
    }
)
_REVIEWED_EXAMPLE_RATIONALE = "Synthetic example for offline configuration validation."
_REVIEWED_EXAMPLE_NOTES = "Synthetic data only."


@dataclass(frozen=True)
class Skill:
    path: Path
    frontmatter: dict[str, str]
    body: str


def parse_skill(path: Path) -> Skill:
    parsed = parse_skill_bytes(path.read_bytes(), path.parent.name)
    return Skill(path, parsed.frontmatter, parsed.body)


def declared_resources(skill: Skill) -> tuple[PurePosixPath, ...]:
    return tuple(
        PurePosixPath(path) for path in declared_skill_resources(skill.body.encode("utf-8"))
    )


def _regular_confined(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("resource is outside its root") from exc
    current = root
    if current.is_symlink():
        raise ValueError("symlink resource is forbidden")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink resource is forbidden")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("missing or unconfined resource")


def validate_skill_contracts(root: Path) -> tuple[str, ...]:
    errors = []
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        try:
            _regular_confined(root, path)
            skill = parse_skill(path)
            for resource in declared_resources(skill):
                _regular_confined(path.parent, path.parent / resource)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
    return tuple(errors)


def validate_workflow(path: Path) -> tuple[str, ...]:
    try:
        validate_workflow_bytes(path.read_bytes())
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return (f"{path.name}: {exc}",)
    return ()


def validate_product_a_contracts(root: Path) -> tuple[str, ...]:
    errors = list(validate_skill_contracts(root))
    if {path.parent.name for path in (root / "skills").glob("*/SKILL.md")} != {
        "market-regime",
        "premarket-research",
        "watchlist-management",
    }:
        errors.append("Product A requires exactly three canonical skills")
    manifest = root / "skills/premarket-research/references/workflow-contract.yaml"
    if set((root / "skills").rglob("*.yaml")) != {manifest}:
        errors.append("Product A requires exactly one adjacent workflow manifest")
    errors.extend(validate_workflow(manifest))
    if any((root / "skills").glob("*/scripts/*")):
        errors.append("skill-local executable scripts are forbidden")
    errors.extend(validate_configuration_examples(root))
    return tuple(errors)


def _example_symbol_values(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and (key == "symbol" or key.endswith(("_symbol", "_symbols"))):
                if isinstance(item, str):
                    yield item
                elif isinstance(item, (tuple, list)):
                    yield from (symbol for symbol in item if isinstance(symbol, str))
            yield from _example_symbol_values(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _example_symbol_values(item)


def validate_configuration_examples(root: Path) -> tuple[str, ...]:
    """Validate tracked example configs and their reviewed, de-identified values."""
    example_root = root / "config" / "examples"
    try:
        for filename in FIXED_CONFIGURATION_FILES.values():
            _regular_confined(root, example_root / filename)
        configuration = load_configuration(example_root)
        example_symbols = tuple(
            symbol
            for filename in FIXED_CONFIGURATION_FILES.values()
            for symbol in _example_symbol_values(
                yaml.safe_load((example_root / filename).read_text(encoding="utf-8"))
            )
        )
    except (OSError, ValueError, yaml.YAMLError):
        return ("Product A configuration examples must be safe, present, and model-valid",)

    errors = []
    if any(symbol not in _REVIEWED_EXAMPLE_SYMBOLS for symbol in example_symbols):
        errors.append("configuration example symbols must use reviewed de-identified samples")
    if any(
        item.research_rationale != _REVIEWED_EXAMPLE_RATIONALE
        for item in configuration.watchlist.items
    ):
        errors.append("watchlist example rationales must remain reviewed and de-identified")
    if any(
        item.notes is not None and item.notes != _REVIEWED_EXAMPLE_NOTES
        for item in configuration.watchlist.items
    ):
        errors.append("watchlist example notes must remain reviewed and de-identified")
    return tuple(errors)


def validate_documentation(root: Path) -> tuple[str, ...]:
    errors = []
    documents = (
        root / "README.md",
        root / "skills/README.md",
        root / "docs/architecture/v0.1-boundaries.md",
        root / "docs/operations/scheduling-and-recovery.md",
        root / "docs/operations/release-readiness.md",
    )
    for path in documents:
        if not path.is_file():
            errors.append(f"missing documentation: {path.relative_to(root)}")
            continue
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://")):
                continue
            link_path, _, anchor = target.partition("#")
            target_path = path.parent / link_path if link_path else path
            if not target_path.is_file() or not target_path.resolve().is_relative_to(
                root.resolve()
            ):
                errors.append(f"broken or unconfined link: {path.relative_to(root)} -> {target}")
            elif anchor:
                headings = re.findall(
                    r"^#{1,6} (.+)$", target_path.read_text(encoding="utf-8"), re.MULTILINE
                )
                anchors = {
                    re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                    for heading in headings
                }
                if anchor not in anchors:
                    errors.append(f"missing internal heading: {target}")
    index = documents[1]
    if index.is_file() and any(
        line and not line.startswith(("# ", "- ["))
        for line in index.read_text(encoding="utf-8").splitlines()
    ):
        errors.append("skills README must be a link index only")
    architecture = documents[2]
    if architecture.is_file():
        text = architecture.read_text(encoding="utf-8")
        authorities = (
            "Approved Product A specification",
            "Python typed domain",
            "Generated JSON Schemas",
            "workflow-contract.yaml",
            "SKILL.md",
            "Canonical synthesis prompt",
            "Canonical report template",
            "Immutable JSON run bundle",
            "Derived documentation",
        )
        positions = [text.find(value) for value in authorities]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            errors.append("architecture must state the canonical authority hierarchy in order")
        if "does not rewrite history" not in text:
            errors.append("architecture must preserve frozen historical authority")
    schedule = documents[3]
    if schedule.is_file():
        text = schedule.read_text(encoding="utf-8")
        for required in (
            "PAUSED",
            "premarket-research",
            "frozen evidence",
            "deterministic truth",
            "human review",
            "no execution",
        ):
            if required not in text:
                errors.append(f"schedule documentation missing {required}")
        if any(name in text for name in OPERATION_NAMES):
            errors.append("saved scheduling prompt must not duplicate MCP operation order")
    release = documents[4]
    if release.is_file():
        text = release.read_text(encoding="utf-8")
        release_facts = (
            ("Schedule activation remains PAUSED.", "PAUSED schedule state"),
            ("The local doctor does not call a provider.", "provider-free local doctor"),
            ("Missing exact-head CI evidence is PENDING.", "pending missing exact-head evidence"),
        )
        for required, description in release_facts:
            if required not in text:
                errors.append(f"release readiness documentation missing {description}")
    return tuple(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = (*validate_product_a_contracts(args.root), *validate_documentation(args.root))
    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
