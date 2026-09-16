"""Serialization and immutability at the Product A contract boundary."""

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from finance_research_agent.domain.models import StrictModel
from finance_research_agent.domain.types import (
    FrozenMap,
    JsonValue,
    PositiveDecimal,
    UtcDatetime,
    canonical_bytes,
)


class Example(StrictModel):
    count: int
    value: PositiveDecimal
    observed_at: UtcDatetime
    metadata: FrozenMap[str, JsonValue]


def example(**changes: object) -> Example:
    return Example.model_validate(
        {
            "count": 1,
            "value": Decimal("192.3400"),
            "observed_at": datetime(2026, 9, 16, 12, 45, tzinfo=UTC),
            "metadata": FrozenMap({"z": (True, None), "a": "source"}),
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": True},
        {"count": "1"},
        {"count": True},
        {"value": 192.34},
        {"value": "192.34"},
        {"observed_at": "2026-09-16T12:45:00Z"},
        {"schema_version": "99"},
    ],
)
def test_strict_models_reject_extra_fields_and_incompatible_python_values(changes: dict) -> None:
    with pytest.raises(ValidationError):
        example(**changes)


def test_strict_model_is_frozen() -> None:
    value = example()
    with pytest.raises(ValidationError, match="frozen"):
        value.count = 2


@pytest.mark.parametrize(
    "timestamp",
    [datetime(2026, 9, 16), datetime(2026, 9, 16, tzinfo=timezone(timedelta(hours=8)))],
)
def test_machine_timestamps_reject_naive_and_non_utc_offsets(timestamp: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        example(observed_at=timestamp)


def test_zero_offset_datetime_is_preserved_without_timezone_replacement() -> None:
    timestamp = datetime(2026, 9, 16, tzinfo=timezone(timedelta(0), "Synthetic UTC"))
    assert example(observed_at=timestamp).observed_at is timestamp


def test_canonical_bytes_preserve_decimal_scale_and_utc_and_sort_object_keys() -> None:
    assert canonical_bytes(example()) == (
        b'{"count":1,"metadata":{"a":"source","z":[true,null]},'
        b'"observed_at":"2026-09-16T12:45:00Z","schema_version":"0.1",'
        b'"value":"192.3400"}'
    )


def test_json_round_trip_accepts_wire_timestamps_and_decimals() -> None:
    original = example()
    restored = Example.model_validate_json(canonical_bytes(original))
    assert restored == original
    assert isinstance(restored.value, Decimal)
    assert isinstance(restored.metadata, FrozenMap)
    assert restored.observed_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Inf")])
def test_price_values_reject_nonpositive_and_nonfinite_decimals(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        example(value=value)


def test_frozen_map_detaches_nested_inputs_and_serializes_as_a_json_object() -> None:
    nested = {"values": ["original"]}
    mapping = FrozenMap({"nested": nested})
    value = example(metadata=mapping)
    before = canonical_bytes(value)
    nested["values"].append("changed")
    assert canonical_bytes(value) == before
    assert value.metadata["nested"]["values"] == ("original",)
    with pytest.raises(TypeError):
        value.metadata["new"] = 1
    with pytest.raises(TypeError):
        value.metadata["nested"]["new"] = 1
    with pytest.raises((AttributeError, TypeError)):
        value.metadata._items = ()
    assert json.loads(before)["metadata"] == {"nested": {"values": ["original"]}}


@pytest.mark.parametrize("pairs", [[("a", 1), ("a", 2)], [(1, "invalid")]])
def test_frozen_map_rejects_duplicate_or_nonstring_keys(pairs: list) -> None:
    with pytest.raises(ValueError):
        FrozenMap(pairs)


def test_pydantic_map_validation_does_not_silently_collapse_duplicate_mapping_keys() -> None:
    class DuplicateMapping(Mapping[str, int]):
        def __getitem__(self, key: str) -> int:
            return 1

        def __iter__(self) -> Iterator[str]:
            return iter(("a", "a"))

        def __len__(self) -> int:
            return 2

    with pytest.raises(ValidationError, match="unique"):
        TypeAdapter(FrozenMap[str, int]).validate_python(DuplicateMapping())


@pytest.mark.parametrize("value", [object(), Decimal("1"), float("nan"), {"bad"}])
def test_json_value_map_rejects_non_json_values(value: object) -> None:
    with pytest.raises(ValidationError):
        example(metadata=FrozenMap({"invalid": value}))


def test_frozen_map_order_is_canonical_without_reordering_array_values() -> None:
    left = example(metadata=FrozenMap([("b", ("ev_2", "ev_1")), ("a", 1)]))
    right = example(metadata=FrozenMap([("a", 1), ("b", ("ev_2", "ev_1"))]))
    assert tuple(left.metadata) == ("a", "b")
    assert canonical_bytes(left) == canonical_bytes(right)
    assert left.metadata["b"] == ("ev_2", "ev_1")


def test_frozen_map_pydantic_hook_validates_generic_values_and_emits_object_schema() -> None:
    adapter = TypeAdapter(FrozenMap[str, int], config={"strict": True})
    assert adapter.validate_python({"a": 1}) == FrozenMap({"a": 1})
    with pytest.raises(ValidationError):
        adapter.validate_python({"a": "1"})
    with pytest.raises(ValidationError):
        adapter.validate_python({1: 1})
    assert adapter.dump_json(adapter.validate_python({"a": 1})) == b'{"a":1}'
    assert adapter.json_schema()["type"] == "object"
    assert adapter.json_schema()["additionalProperties"] == {"type": "integer"}


@pytest.mark.parametrize("metadata", [{"a": "x" * 8193}, {"a": tuple(range(129))}])
def test_structured_json_values_are_bounded(metadata: dict) -> None:
    with pytest.raises(ValidationError):
        example(metadata=FrozenMap(metadata))


def test_structured_map_size_is_bounded() -> None:
    with pytest.raises(ValueError):
        FrozenMap({str(index): index for index in range(129)})
