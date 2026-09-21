"""Strict, immutable Product A configuration policies."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from finance_research_agent.domain.regime import Regime, RegimePolicy
from finance_research_agent.domain.types import FrozenMap

_ASCII_TICKER = re.compile(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$")
_SLUG = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_VERSION = re.compile(r"^[1-9][0-9]*$")
_PRINTABLE = set(chr(i) for i in range(32, 127))
_DOMAIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_ADAPTER = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_SOURCE_MAP_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_HTTPS_URL_ADAPTER = TypeAdapter(HttpUrl)


def _percent_decode(value: str) -> str:
    return re.sub(
        r"%([0-9A-Fa-f]{2})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    @model_validator(mode="before")
    @classmethod
    def copy_collection_inputs(cls, value: object) -> object:
        """Copy YAML-style arrays into immutable tuple inputs without reordering."""

        def copy(item: object) -> object:
            if isinstance(item, list):
                return tuple(copy(child) for child in item)
            if isinstance(item, dict):
                return {key: copy(child) for key, child in item.items()}
            return item

        return copy(value)


def _english(value: str, field: str, *, max_length: int = 1000) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must not be blank")
    if len(value) > max_length:
        raise ValueError(f"{field} exceeds the maximum length of {max_length}")
    if any(character not in _PRINTABLE for character in value):
        raise ValueError(f"English normalization required for {field}")
    if any(unicodedata.category(character) in {"Cf", "Cc"} for character in value):
        raise ValueError(f"{field} contains a control or format character")
    return value


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, (str, Decimal)) or isinstance(value, bool):
        raise ValueError(f"{field} must be a decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal string") from error
    if not result.is_finite() or (positive and result <= 0):
        message = f"{field} must be finite and positive" if positive else f"{field} must be finite"
        raise ValueError(message)
    return result


def _ticker(value: str, field: str = "symbol") -> str:
    if (
        type(value) is not str
        or len(value) > 16
        or not value.isascii()
        or _ASCII_TICKER.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be uppercase ASCII ticker text")
    return value


def validate_ticker(value: str, field: str = "symbol") -> str:
    """Validate a caller-provided ticker before any mutation is attempted."""

    return _ticker(value, field)


DecimalString = Annotated[Decimal, Field(strict=False)]


class WatchlistItem(PolicyModel):
    symbol: str
    role: Literal["CORE_MONITOR", "SATELLITE_ELIGIBLE", "RESEARCH_ONLY"]
    research_rationale: str
    tags: tuple[str, ...] = ()
    priority: int = Field(default=0, ge=0, le=100)
    research_horizon: Literal["SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"] = "MEDIUM_TERM"
    expires_on: date | None = None
    benchmark_symbol: str | None = None
    sector_proxy_symbol: str | None = None
    official_sources: tuple[str, ...] = ()
    notes: str | None = None

    @field_validator("symbol", "benchmark_symbol", "sector_proxy_symbol")
    @classmethod
    def valid_ticker(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _ticker(value)

    @field_validator("research_rationale", "notes")
    @classmethod
    def valid_prose(cls, value: str | None, info: object) -> str | None:
        return None if value is None else _english(value, "stored prose")

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 16 or len(value) != len(set(value)) or any(
            type(tag) is not str or len(tag) > 32 or _SLUG.fullmatch(tag) is None
            for tag in value
        ):
            raise ValueError("tags must be unique bounded ASCII slugs")
        return value

    @field_validator("official_sources")
    @classmethod
    def valid_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 8 or len(value) != len(set(value)):
            raise ValueError("official_sources must be unique")
        for source in value:
            if (
                type(source) is not str
                or len(source) > 2048
                or any(
                    unicodedata.category(character) in {"Cc", "Cf"}
                    or character.isspace()
                    for character in source
                )
            ):
                raise ValueError("official sources must be safe HTTPS URLs")
            try:
                parsed = _HTTPS_URL_ADAPTER.validate_python(source, strict=True)
            except ValueError as error:
                raise ValueError("official sources must be safe HTTPS URLs") from error
            if (
                parsed.scheme != "https"
                or not parsed.host
                or parsed.username is not None
                or parsed.password is not None
                or "#" in source
            ):
                raise ValueError("official sources must be safe HTTPS URLs")
            authority_and_path = source.removeprefix("https://").split("?", 1)[0]
            path = authority_and_path.partition("/")[2].replace("\\", "/")
            while True:
                decoded = _percent_decode(path).replace("\\", "/")
                if decoded == path:
                    break
                path = decoded
            if any(part in {".", ".."} for part in path.split("/")):
                raise ValueError("official sources contain an invalid URL")
        return value


class WatchlistConfig(PolicyModel):
    version: str
    items: tuple[WatchlistItem, ...] = ()

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("version must be a positive decimal string")
        return value

    @model_validator(mode="after")
    def unique_bounded_symbols(self) -> WatchlistConfig:
        if len(self.items) > 30:
            raise ValueError("watchlist supports at most 30 names")
        symbols = tuple(item.symbol for item in self.items)
        if len(symbols) != len(set(symbols)):
            raise ValueError("watchlist symbols must be unique")
        return self


class RiskPolicy(PolicyModel):
    version: str
    sizing_enabled: bool = False
    planning_capital_usd: Decimal | None = None
    max_risk_per_trade_pct: Decimal | None = None
    max_position_pct: Decimal | None = None
    minimum_reward_risk_ratio: Decimal | None = None
    max_total_portfolio_heat_pct: Decimal | None = None
    existing_portfolio_heat_pct: Decimal | None = None
    max_concurrent_plan_drafts: int = Field(default=5, ge=0, le=5)
    allow_fractional_units: bool = False
    quantity_increment: Decimal
    regime_risk_multipliers: FrozenMap[str, Decimal]

    @field_validator("version")
    @classmethod
    def version_decimal(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("version must be a positive decimal string")
        return value

    @field_validator("planning_capital_usd", "max_risk_per_trade_pct", "max_position_pct",
                     "minimum_reward_risk_ratio", "max_total_portfolio_heat_pct",
                     "existing_portfolio_heat_pct", "quantity_increment", mode="before")
    @classmethod
    def decimal_strings(cls, value: object, info: object) -> Decimal | None:
        return None if value is None else _decimal(value, "risk value", positive=True)

    @field_validator("regime_risk_multipliers", mode="before")
    @classmethod
    def decimal_multiplier_map(cls, value: object) -> dict[str, Decimal]:
        if not isinstance(value, dict):
            raise ValueError("regime_risk_multipliers must be a mapping")
        return {key: _decimal(item, "regime multiplier") for key, item in value.items()}

    @model_validator(mode="after")
    def valid_risk_map(self) -> RiskPolicy:
        if set(self.regime_risk_multipliers) != {regime.name for regime in Regime}:
            raise ValueError("regime_risk_multipliers must define every regime")
        if any(
            value < Decimal("0") or value > Decimal("1")
            for value in self.regime_risk_multipliers.values()
        ):
            raise ValueError("regime risk multipliers must be between zero and one")
        return self


class SetupPolicy(PolicyModel):
    version: str
    required_historical_sessions: int = Field(ge=1)
    minimum_price: Decimal
    minimum_median_dollar_volume: Decimal
    moving_average_windows: tuple[int, ...]
    trend_slope_windows: tuple[int, ...]
    breakout_lookback: int = Field(ge=1)
    atr_window: int = Field(ge=1)
    entry_zone_atr_buffers: tuple[Decimal, ...]
    extension_limits: tuple[Decimal, ...]
    pullback_support_tolerances: tuple[Decimal, ...]
    restrengthening_conditions: tuple[str, ...]
    minimum_reward_to_risk: Decimal
    plan_lifetime_sessions: int = Field(ge=1)
    earnings_blackout_sessions: int = Field(ge=0)
    score_weights: tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]
    penalty_names: tuple[
        Literal[
            "EXTENSION_PENALTY",
            "EVENT_UNCERTAINTY_PENALTY",
            "CORRELATION_CONCENTRATION_PENALTY",
            "DATA_QUALITY_PENALTY",
        ],
        ...,
    ]

    @field_validator("version")
    @classmethod
    def version_decimal(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("version must be a positive decimal string")
        return value

    @field_validator(
        "minimum_price",
        "minimum_median_dollar_volume",
        "minimum_reward_to_risk",
        "entry_zone_atr_buffers",
        "extension_limits",
        "pullback_support_tolerances",
        "score_weights",
        mode="before",
    )
    @classmethod
    def setup_decimals(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name or "setup value"
        if field_name in {
            "entry_zone_atr_buffers",
            "extension_limits",
            "pullback_support_tolerances",
            "score_weights",
        }:
            if not isinstance(value, tuple):
                raise ValueError(f"{field_name} must be an immutable collection")
            return tuple(_decimal(item, field_name, positive=True) for item in value)
        return _decimal(value, field_name, positive=True)

    @model_validator(mode="after")
    def strict_setup_collections(self) -> SetupPolicy:
        if len(self.score_weights) != 6 or any(
            not value.is_finite() or value <= 0 for value in self.score_weights
        ):
            raise ValueError("score_weights must contain exactly six positive finite values")
        required_penalties = {
            "EXTENSION_PENALTY",
            "EVENT_UNCERTAINTY_PENALTY",
            "CORRELATION_CONCENTRATION_PENALTY",
            "DATA_QUALITY_PENALTY",
        }
        if len(self.penalty_names) != 4 or set(self.penalty_names) != required_penalties:
            raise ValueError("penalty_names must contain exactly the four required penalties")
        return self


class SourcePolicy(PolicyModel):
    version: str
    allowed_adapters: tuple[str, ...]
    allowed_https_domains: tuple[str, ...]
    allowed_hosts_by_adapter: FrozenMap[str, tuple[str, ...]] | None = None
    allowed_ports_by_host: FrozenMap[str, tuple[int, ...]] = FrozenMap({})
    freshness_by_data_type: FrozenMap[str, int]
    cache_retention_seconds: int = Field(ge=0)
    request_deadline_seconds: Decimal
    retry_attempts: int = Field(ge=0)
    retry_backoff_seconds: Decimal
    retry_jitter_seconds: Decimal
    per_run_request_budgets: FrozenMap[str, int]
    allow_redirects: bool = False
    maximum_response_bytes: int = Field(ge=1)
    allowed_content_types: tuple[str, ...]
    excerpt_limits: FrozenMap[str, int]
    licensed_content_persistence: Literal["NONE", "METADATA_ONLY", "ALLOWED"] = "NONE"

    @field_validator("version")
    @classmethod
    def version_decimal(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("version must be a positive decimal string")
        return value

    @field_validator(
        "request_deadline_seconds", "retry_backoff_seconds", "retry_jitter_seconds", mode="before"
    )
    @classmethod
    def source_decimals(cls, value: object, info: object) -> Decimal:
        return _decimal(value, "source timing", positive=True)

    @field_validator("allowed_adapters")
    @classmethod
    def valid_adapters(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= 16 or len(value) != len(set(value)) or any(
            type(item) is not str or len(item) > 64 or _ADAPTER.fullmatch(item) is None
            for item in value
        ):
            raise ValueError("allowed_adapters must be a bounded non-empty unique collection")
        return value

    @field_validator("allowed_https_domains")
    @classmethod
    def valid_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= 64 or len(value) != len(set(value)) or any(
            type(item) is not str
            or len(item) > 253
            or _DOMAIN.fullmatch(item) is None
            or ".." in item
            for item in value
        ):
            raise ValueError("allowed_https_domains must be bounded non-empty unique domains")
        return value

    @field_validator("allowed_hosts_by_adapter")
    @classmethod
    def valid_adapter_hosts(
        cls, value: FrozenMap[str, tuple[str, ...]] | None
    ) -> FrozenMap[str, tuple[str, ...]] | None:
        if value is None:
            return None
        if not 1 <= len(value) <= 16:
            raise ValueError("allowed_hosts_by_adapter must be bounded and non-empty")
        for adapter, hosts in value.items():
            if _ADAPTER.fullmatch(adapter) is None or not 1 <= len(hosts) <= 64:
                raise ValueError("adapter host entries must be bounded")
            if len(hosts) != len(set(hosts)) or any(
                type(host) is not str
                or not 1 <= len(host) <= 253
                or _DOMAIN.fullmatch(host) is None
                for host in hosts
            ):
                raise ValueError("adapter host entries must be unique domain names")
        return value

    @field_validator("allowed_ports_by_host")
    @classmethod
    def valid_host_ports(
        cls, value: FrozenMap[str, tuple[int, ...]]
    ) -> FrozenMap[str, tuple[int, ...]]:
        if len(value) > 64:
            raise ValueError("allowed_ports_by_host must be bounded")
        for host, ports in value.items():
            if _DOMAIN.fullmatch(host) is None or not 1 <= len(ports) <= 16:
                raise ValueError("host port entries must be bounded")
            if len(ports) != len(set(ports)) or any(
                type(port) is not int or not 1 <= port <= 65535 for port in ports
            ):
                raise ValueError("host ports must be unique valid integers")
        return value

    @field_validator("allowed_content_types")
    @classmethod
    def valid_content_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= 32 or len(value) != len(set(value)) or any(
            type(item) is not str or not item.strip() or len(item) > 128 for item in value
        ):
            raise ValueError("allowed_content_types must be bounded non-empty unique values")
        return value

    @field_validator("freshness_by_data_type", "per_run_request_budgets", "excerpt_limits")
    @classmethod
    def valid_non_negative_maps(cls, value: FrozenMap[str, int]) -> FrozenMap[str, int]:
        if not 1 <= len(value) <= 64:
            raise ValueError("source policy maps must be bounded and non-empty")
        if any(
            type(key) is not str
            or not 1 <= len(key) <= 64
            or _SOURCE_MAP_KEY.fullmatch(key) is None
            or type(item) is not int
            or item < 0
            for key, item in value.items()
        ):
            raise ValueError("source policy map entries must be bounded and non-negative")
        return value

    @model_validator(mode="after")
    def adapter_host_keys_are_allowed(self) -> SourcePolicy:
        if self.allowed_hosts_by_adapter is not None and set(self.allowed_hosts_by_adapter) != set(
            self.allowed_adapters
        ):
            raise ValueError("allowed_hosts_by_adapter must cover every allowed adapter")
        return self


class AppConfiguration(PolicyModel):
    watchlist: WatchlistConfig
    risk: RiskPolicy
    regime: RegimePolicy
    setup: SetupPolicy
    source: SourcePolicy


def canonical_model_hash(model: BaseModel | object) -> str:
    """Hash validated model content, independently of YAML formatting."""
    if isinstance(model, BaseModel):
        value = model.model_dump(mode="json")
    elif is_dataclass(model):
        value = asdict(cast(Any, model))
    else:
        raise TypeError("canonical_model_hash requires a validated model")
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return sha256(payload.encode("utf-8")).hexdigest()
