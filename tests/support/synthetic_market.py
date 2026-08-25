from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType

from finance_research_agent.domain.market import DailyBar, MarketDataSource, MarketSnapshot
from finance_research_agent.domain.regime import Regime, RegimeComponentState

CUTOFF = datetime(2026, 8, 25, 23, 59, tzinfo=UTC)
SECTORS = (
    "XLC",
    "XLY",
    "XLP",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLB",
    "XLRE",
    "XLK",
    "XLU",
)
CYCLICAL = ("XLY", "XLE", "XLF", "XLI", "XLB", "XLK")
DEFENSIVE = ("XLP", "XLV", "XLU")
REQUIRED_SYMBOLS = tuple(sorted({"SPY", "QQQ", "IWM", "HYG", "LQD", *SECTORS}))


@dataclass(frozen=True, slots=True)
class SyntheticRegimeCase:
    case_id: str
    snapshots: Mapping[str, MarketSnapshot]
    expected_regime: Regime
    expected_score: Decimal | None
    expected_components: tuple[RegimeComponentState, ...]


def compound_closes(
    *,
    count: int = 273,
    daily_return: Decimal,
    tail_returns: tuple[Decimal, ...] = (),
) -> tuple[Decimal, ...]:
    if len(tail_returns) > count - 1:
        raise ValueError("tail returns exceed requested history")
    returns = (daily_return,) * (count - 1 - len(tail_returns)) + tail_returns
    closes = [Decimal("100")]
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        for value in returns:
            closes.append(closes[-1] * (Decimal(1) + value))
    return tuple(closes)


def make_snapshot(
    *,
    symbol: str,
    closes: tuple[Decimal, ...],
    case_id: str,
    range_fraction: Decimal = Decimal("0.005"),
    tail_range_fractions: tuple[Decimal, ...] = (),
) -> MarketSnapshot:
    if len(tail_range_fractions) > len(closes):
        raise ValueError("tail ranges exceed close history")
    ranges = (range_fraction,) * (len(closes) - len(tail_range_fractions)) + tail_range_fractions
    start = CUTOFF.date() - timedelta(days=len(closes) - 1)
    bars: list[DailyBar] = []
    previous_close = closes[0]
    for index, (close, daily_range) in enumerate(zip(closes, ranges, strict=True)):
        open_price = previous_close
        high = max(open_price, close) * (Decimal(1) + daily_range)
        low = min(open_price, close) * (Decimal(1) - daily_range)
        bars.append(
            DailyBar(
                session_date=start + timedelta(days=index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1_000_000,
            )
        )
        previous_close = close
    return MarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id=f"{case_id}-{symbol.lower()}",
        symbol=symbol,
        as_of=CUTOFF,
        currency="USD",
        source=MarketDataSource.SYNTHETIC,
        completed_daily_bars=tuple(bars),
        quality_flags=(),
    )


def _risk_on_rate(symbol: str) -> Decimal:
    if symbol == "SPY":
        return Decimal("0.001")
    if symbol == "QQQ":
        return Decimal("0.002")
    if symbol == "IWM":
        return Decimal("0.0015")
    if symbol == "HYG":
        return Decimal("0.0008")
    if symbol == "LQD":
        return Decimal("0.0002")
    if symbol in CYCLICAL:
        return Decimal("0.0018")
    if symbol in DEFENSIVE:
        return Decimal("0.0005")
    return Decimal("0.001")


def _risk_off_rate(symbol: str) -> Decimal:
    if symbol == "QQQ":
        return Decimal("-0.003")
    if symbol == "IWM":
        return Decimal("-0.004")
    if symbol == "HYG":
        return Decimal("-0.003")
    if symbol == "LQD":
        return Decimal("-0.0005")
    if symbol in CYCLICAL:
        return Decimal("-0.004")
    if symbol in DEFENSIVE:
        return Decimal("-0.001")
    return Decimal("-0.0025")


def make_regime_case(case_id: str) -> SyntheticRegimeCase:
    snapshots: dict[str, MarketSnapshot] = {}
    if case_id == "risk-on":
        for symbol in REQUIRED_SYMBOLS:
            snapshots[symbol] = make_snapshot(
                symbol=symbol,
                closes=compound_closes(daily_return=_risk_on_rate(symbol)),
                case_id=case_id,
                tail_range_fractions=(Decimal("0.003"),) * 20 if symbol == "SPY" else (),
            )
        return SyntheticRegimeCase(
            case_id=case_id,
            snapshots=MappingProxyType(snapshots),
            expected_regime=Regime.PERMISSIVE,
            expected_score=Decimal("100"),
            expected_components=(RegimeComponentState.POSITIVE,) * 5,
        )

    if case_id == "neutral":
        for symbol in REQUIRED_SYMBOLS:
            snapshots[symbol] = make_snapshot(
                symbol=symbol,
                closes=compound_closes(daily_return=Decimal(0)),
                case_id=case_id,
            )
        return SyntheticRegimeCase(
            case_id=case_id,
            snapshots=MappingProxyType(snapshots),
            expected_regime=Regime.NEUTRAL,
            expected_score=Decimal("-10"),
            expected_components=(
                RegimeComponentState.MIXED,
                RegimeComponentState.NEGATIVE,
                RegimeComponentState.MIXED,
                RegimeComponentState.POSITIVE,
                RegimeComponentState.MIXED,
            ),
        )

    if case_id == "risk-off":
        volatile_tail = (Decimal("0.04"), Decimal("-0.041")) * 10
        snapshots["SPY"] = make_snapshot(
            symbol="SPY",
            closes=compound_closes(
                daily_return=Decimal("-0.002"),
                tail_returns=volatile_tail,
            ),
            case_id=case_id,
            tail_range_fractions=(Decimal("0.06"),) * 20,
        )
        for symbol in REQUIRED_SYMBOLS:
            if symbol == "SPY":
                continue
            snapshots[symbol] = make_snapshot(
                symbol=symbol,
                closes=compound_closes(daily_return=_risk_off_rate(symbol)),
                case_id=case_id,
            )
        return SyntheticRegimeCase(
            case_id=case_id,
            snapshots=MappingProxyType(snapshots),
            expected_regime=Regime.DEFENSIVE,
            expected_score=Decimal("-100"),
            expected_components=(RegimeComponentState.NEGATIVE,) * 5,
        )

    raise ValueError(f"unknown synthetic case: {case_id}")
