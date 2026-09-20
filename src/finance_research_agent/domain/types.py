"""Immutable structured values and deterministic JSON for foundation contracts."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, TypeVar, cast, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    PlainSerializer,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

_Key = TypeVar("_Key", bound=str)
_Value = TypeVar("_Value")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenMap(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True, init=False)
class FrozenMap(Mapping[_Key, _Value]):
    """Copied, key-sorted mapping; array order is retained and keys never coerce.

    Nested JSON dictionaries and arrays are detached and frozen. Generic value
    validation belongs to the Pydantic field using this type.
    """

    _items: tuple[tuple[_Key, _Value], ...]

    def __init__(self, values: Mapping[_Key, _Value] | Iterable[tuple[_Key, _Value]]) -> None:
        pairs = values.items() if isinstance(values, Mapping) else values
        copied: dict[_Key, _Value] = {}
        for key, value in pairs:
            if type(key) is not str:
                raise ValueError("FrozenMap keys must be strings")
            if key in copied:
                raise ValueError("FrozenMap keys must be unique")
            if len(copied) >= 128:
                raise ValueError("FrozenMap permits at most 128 entries")
            copied[cast(_Key, key)] = _freeze(value)
        object.__setattr__(self, "_items", tuple(sorted(copied.items())))

    def __getitem__(self, key: _Key) -> _Value:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[_Key]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        key_type, value_type = get_args(source_type)
        dictionary = core_schema.dict_schema(
            keys_schema=handler.generate_schema(key_type),
            values_schema=handler.generate_schema(value_type),
            strict=True,
            max_length=128,
        )

        def mapping_input(value: Any) -> dict[Any, Any]:
            if not isinstance(value, Mapping):
                raise ValueError("FrozenMap requires a mapping")
            return dict(FrozenMap(value))

        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.no_info_before_validator_function(mapping_input, dictionary),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: dict(value), return_schema=dictionary
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        result = handler(schema)
        if "patternProperties" in result:
            result["additionalProperties"] = False
        return result


def _json_value(value: Any) -> Any:
    if type(value) not in (str, int, float, bool, type(None), tuple) and not isinstance(
        value, FrozenMap
    ):
        raise ValueError("structured fields require JSON values")
    return value


type JsonValue = Annotated[
    Annotated[str, Field(max_length=8192)]
    | int
    | Annotated[float, Field(allow_inf_nan=False)]
    | bool
    | None
    | Annotated[tuple[JsonValue, ...], Field(max_length=128)]
    | FrozenMap[str, JsonValue],
    BeforeValidator(_json_value),
]


def utc_datetime(value: datetime) -> datetime:
    """Reject non-UTC input rather than rewriting the source timestamp."""
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC with zero offset")
    return value


def decimal_json(value: Decimal) -> str:
    """Use a JSON string to retain decimal precision and declared scale."""
    return format(value, "f")


UtcDatetime = Annotated[
    datetime,
    AfterValidator(utc_datetime),
    Field(json_schema_extra={"pattern": r"(?:Z|\+00:00)$"}),
]
PositiveDecimal = Annotated[
    Decimal,
    Field(
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={"pattern": r"^(?:0*[1-9][0-9]*(?:\.[0-9]+)?|0+\.[0-9]*[1-9][0-9]*)$"},
    ),
    PlainSerializer(decimal_json, return_type=str, when_used="json"),
]


def canonical_bytes(model: BaseModel) -> bytes:
    """Canonical UTF-8 contract representation; never mutate or reorder arrays."""
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
