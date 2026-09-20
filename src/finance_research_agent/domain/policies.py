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
    field_validator,
    model_validator,
)

from finance_research_agent.domain.regime import Regime, RegimePolicy

_ASCII_TICKER = re.compile(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$")
_SLUG = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_VERSION = re.compile(r"^[1-9][0-9]*$")
_PRINTABLE = set(chr(i) for i in range(32, 127))
_HTTPS_URL_ADAPTER = TypeAdapter(HttpUrl)


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


def _english(value: str, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must not be blank")
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
    if type(value) is not str or not value.isascii() or _ASCII_TICKER.fullmatch(value) is None:
        raise ValueError(f"{field} must be uppercase ASCII ticker text")
    return value


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
        if len(value) != len(set(value)) or any(_SLUG.fullmatch(tag) is None for tag in value):
            raise ValueError("tags must be unique bounded ASCII slugs")
        return value

    @field_validator("official_sources")
    @classmethod
    def valid_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("official_sources must be unique")
        for source in value:
            parsed = _HTTPS_URL_ADAPTER.validate_python(source, strict=True)
            if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password:
                raise ValueError("official sources must be safe HTTPS URLs")
            authority_and_path = source.removeprefix("https://").split("?", 1)[0].split("#", 1)[0]
            path = authority_and_path.partition("/")[2]
            if "#" in source or any(
                part in {".", ".."} for part in path.replace("\\", "/").split("/")
            ):
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
    regime_risk_multipliers: dict[str, Decimal]

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
    penalty_names: tuple[Literal["LOW_LIQUIDITY", "EXTENDED", "EVENT_RISK", "DATA_QUALITY"], ...]

    @field_validator("version")
    @classmethod
    def version_decimal(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("version must be a positive decimal string")
        return value

    @field_validator(
        "minimum_price", "minimum_median_dollar_volume", "minimum_reward_to_risk", mode="before"
    )
    @classmethod
    def setup_decimals(cls, value: object, info: object) -> Decimal:
        return _decimal(value, "setup value", positive=True)


class SourcePolicy(PolicyModel):
    version: str
    allowed_adapters: tuple[str, ...]
    allowed_https_domains: tuple[str, ...]
    freshness_by_data_type: dict[str, int]
    cache_retention_seconds: int = Field(ge=0)
    request_deadline_seconds: Decimal
    retry_attempts: int = Field(ge=0)
    retry_backoff_seconds: Decimal
    retry_jitter_seconds: Decimal
    per_run_request_budgets: dict[str, int]
    allow_redirects: bool = False
    maximum_response_bytes: int = Field(ge=1)
    allowed_content_types: tuple[str, ...]
    excerpt_limits: dict[str, int]
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
