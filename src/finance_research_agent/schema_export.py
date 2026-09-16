"""Deterministic R1 JSON-schema export and read-only checked-in drift detection."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from finance_research_agent.domain.models import (
    CapabilityState,
    ComponentVersions,
    ConfigurationSnapshot,
    EventRecord,
    EvidenceItem,
    GateResult,
    InstrumentIdentity,
    PriceObservation,
    RunContext,
    SourceObservation,
    StrictModel,
)

SCHEMA_MODELS: Mapping[str, type[StrictModel]] = MappingProxyType(
    {
        "capability-state.schema.json": CapabilityState,
        "component-versions.schema.json": ComponentVersions,
        "configuration-snapshot.schema.json": ConfigurationSnapshot,
        "event-record.schema.json": EventRecord,
        "evidence-item.schema.json": EvidenceItem,
        "gate-result.schema.json": GateResult,
        "instrument-identity.schema.json": InstrumentIdentity,
        "price-observation.schema.json": PriceObservation,
        "run-context.schema.json": RunContext,
        "source-observation.schema.json": SourceObservation,
    }
)


def _schema_bytes(model: type[StrictModel]) -> bytes:
    payload = json.dumps(
        model.model_json_schema(mode="serialization"),
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
    )
    return (payload + "\n").encode("utf-8")


def export_schemas(output_dir: Path) -> tuple[Path, ...]:
    """Write only the registered schemas, in deterministic filename order."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in sorted(SCHEMA_MODELS.items()):
        path = output_dir / filename
        path.write_bytes(_schema_bytes(model))
        written.append(path)
    return tuple(written)


def check_schemas(output_dir: Path) -> tuple[str, ...]:
    """Return missing, changed, or unregistered schema filenames; never write."""
    differences = {
        path.name for path in output_dir.glob("*.schema.json") if path.name not in SCHEMA_MODELS
    }
    for filename, model in SCHEMA_MODELS.items():
        path = output_dir / filename
        if not path.is_file() or path.read_bytes() != _schema_bytes(model):
            differences.add(filename)
    return tuple(sorted(differences))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("schemas"))
    parser.add_argument("--check", action="store_true", help="Check without rewriting schemas")
    options = parser.parse_args()
    if options.check:
        differences = check_schemas(options.output_dir)
        for filename in differences:
            print(f"Schema drift: {filename}")
        return int(bool(differences))
    export_schemas(options.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
