import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from finance_research_agent.schema_export import SCHEMA_MODELS, check_schemas, export_schemas

SCHEMA_NAMES = {
    "capability-state.schema.json",
    "component-versions.schema.json",
    "configuration-snapshot.schema.json",
    "event-record.schema.json",
    "evidence-item.schema.json",
    "gate-result.schema.json",
    "instrument-identity.schema.json",
    "market-snapshot.schema.json",
    "metric-result.schema.json",
    "price-observation.schema.json",
    "run-context.schema.json",
    "source-observation.schema.json",
}
ROOT = Path(__file__).resolve().parents[2]


def test_registry_is_immutable_and_contains_only_implemented_contracts() -> None:
    assert set(SCHEMA_MODELS) == SCHEMA_NAMES
    with pytest.raises(TypeError):
        SCHEMA_MODELS["invented.schema.json"] = object


def test_every_schema_export_is_stable_sorted_newline_terminated_json(tmp_path: Path) -> None:
    paths = export_schemas(tmp_path)
    first = {path.name: path.read_bytes() for path in paths}
    assert set(first) == SCHEMA_NAMES
    assert tuple(path.name for path in paths) == tuple(sorted(SCHEMA_NAMES))
    assert first == {path.name: path.read_bytes() for path in export_schemas(tmp_path)}
    for payload in first.values():
        parsed = json.loads(payload)
        assert (
            payload
            == (json.dumps(parsed, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()
        )
        assert parsed["additionalProperties"] is False
        assert parsed["properties"]["schema_version"]["const"] == "0.1"
        for definition in parsed.get("$defs", {}).values():
            if "properties" in definition:
                assert definition["additionalProperties"] is False


def test_generated_schemas_match_the_checked_in_files() -> None:
    assert check_schemas(ROOT / "schemas") == ()


def test_schema_check_detects_changed_missing_and_unregistered_files(tmp_path: Path) -> None:
    export_schemas(tmp_path)
    assert check_schemas(tmp_path) == ()
    (tmp_path / "run-context.schema.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "gate-result.schema.json").unlink()
    (tmp_path / "future-contract.schema.json").write_text("{}\n", encoding="utf-8")
    assert check_schemas(tmp_path) == (
        "future-contract.schema.json",
        "gate-result.schema.json",
        "run-context.schema.json",
    )


def test_schema_preserves_wire_decimal_and_provenance_constraints(tmp_path: Path) -> None:
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "price-observation.schema.json").read_bytes())
    properties = schema["properties"]
    assert properties["value"]["type"] == "string"
    for valid in ("192.3400", "0.001", "10", "1.00"):
        assert re.fullmatch(properties["value"]["pattern"], valid)
    for invalid in ("0", "0.0", "-1", "NaN", "Infinity", "price", "1e2"):
        assert not re.fullmatch(properties["value"]["pattern"], invalid)
    assert properties["observed_at"]["format"] == "date-time"
    assert properties["observed_at"]["pattern"] == r"(?:Z|\+00:00)$"
    assert properties["quality_flags"]["uniqueItems"] is True
    assert schema["$defs"]["Coverage"]["enum"] == ["single_exchange", "consolidated", "unknown"]
    evidence_schema = json.loads((tmp_path / "evidence-item.schema.json").read_bytes())
    assert evidence_schema["properties"]["structured_fields"]["type"] == "object"
    assert evidence_schema["properties"]["source"] == {"$ref": "#/$defs/SourceObservation"}
    metric_schema = json.loads((tmp_path / "metric-result.schema.json").read_bytes())
    assert metric_schema["properties"]["value"]["anyOf"][0]["type"] == "string"
    metric_properties = metric_schema["properties"]
    identifier_pattern = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    for field, max_items in (
        ("input_snapshot_ids", 256),
        ("input_evidence_ids", 256),
        ("quality_flags", 64),
    ):
        assert metric_properties[field]["type"] == "array"
        assert metric_properties[field]["maxItems"] == max_items
        assert metric_properties[field]["uniqueItems"] is True
        assert metric_properties[field]["items"] == {
            "maxLength": 128,
            "minLength": 1,
            "pattern": identifier_pattern,
            "type": "string",
        }
    parameters = metric_properties["parameters"]
    assert parameters["maxItems"] == 64
    assert parameters["items"]["minItems"] == parameters["items"]["maxItems"] == 2
    assert parameters["items"]["prefixItems"][0] == {
        "maxLength": 128,
        "minLength": 1,
        "pattern": identifier_pattern,
        "type": "string",
    }
    assert parameters["items"]["prefixItems"][1] == {
        "maxLength": 256,
        "minLength": 1,
        "type": "string",
    }
    assert metric_properties["calculated_at"]["format"] == "date-time"
    assert metric_properties["calculated_at"]["pattern"] == r"(?:Z|\+00:00)$"
    assert "input_evidence_ids" in metric_schema["required"]
    market_schema = json.loads((tmp_path / "market-snapshot.schema.json").read_bytes())
    assert market_schema["properties"]["completed_daily_bars"]["type"] == "array"
    assert market_schema["properties"]["source_observations"]["type"] == "array"


def test_schema_command_checks_without_rewriting_and_fails_on_drift(tmp_path: Path) -> None:
    export_schemas(tmp_path)
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    command = [
        sys.executable,
        "-m",
        "finance_research_agent.schema_export",
        "--output-dir",
        str(tmp_path),
        "--check",
    ]
    result = subprocess.run(command, env=environment, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
    target = tmp_path / "run-context.schema.json"
    target.write_text("{}\n", encoding="utf-8")
    result = subprocess.run(command, env=environment, capture_output=True, timeout=20)
    assert result.returncode == 1
    assert b"run-context.schema.json" in result.stdout
    assert target.read_bytes() == b"{}\n"


def test_map_schemas_reject_keys_outside_the_declared_identifier_grammar(tmp_path: Path) -> None:
    export_schemas(tmp_path)
    for filename, field in (
        ("run-context.schema.json", "schema_versions"),
        ("component-versions.schema.json", "schema_versions"),
        ("configuration-snapshot.schema.json", "file_hashes"),
        ("evidence-item.schema.json", "structured_fields"),
    ):
        schema = json.loads((tmp_path / filename).read_bytes())
        mapping = schema["properties"][field]
        assert mapping["patternProperties"]
        assert mapping["additionalProperties"] is False
