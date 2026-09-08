"""Immutable temporal contracts for future walk-forward evaluation, without replay."""

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["WalkForwardWindow", "validate_walk_forward_plan"]


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """Closed training and evaluation intervals with strictly separated UTC bounds.

    Timestamps must already be timezone-aware with zero UTC offset. Values are
    preserved without conversion, and each interval must have positive duration.
    """

    train_start: datetime
    train_end: datetime
    eval_start: datetime
    eval_end: datetime

    def __post_init__(self) -> None:
        for field_name in ("train_start", "train_end", "eval_start", "eval_end"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() != timedelta(0)
            ):
                raise ValueError(f"{field_name} must be timezone-aware UTC")
        if self.train_start >= self.train_end:
            raise ValueError("train_start must be before train_end")
        if self.train_end >= self.eval_start:
            raise ValueError("train_end must be before eval_start")
        if self.eval_start >= self.eval_end:
            raise ValueError("eval_start must be before eval_end")


def validate_walk_forward_plan(windows: tuple[WalkForwardWindow, ...]) -> None:
    """Validate a nonempty tuple in caller order without normalization.

    Closed evaluation periods must be strictly separated: the previous eval_end
    must be before the next eval_start, so touching periods are rejected.
    Training periods have no cross-window ordering constraint; later training
    may incorporate previous evaluation history. Invalid plans raise ValueError.
    """

    if not isinstance(windows, tuple):
        raise ValueError("walk-forward windows must be an immutable tuple")
    if not windows:
        raise ValueError("walk-forward windows must not be empty")
    seen: set[WalkForwardWindow] = set()
    for index, window in enumerate(windows):
        if not isinstance(window, WalkForwardWindow):
            raise ValueError(f"window at index {index} must be a WalkForwardWindow")
        if window in seen:
            raise ValueError(f"duplicate walk-forward window at index {index}")
        seen.add(window)
    for previous, current in zip(windows, windows[1:]):
        if previous.eval_end >= current.eval_start:
            raise ValueError(
                "evaluation periods must be chronologically ordered and strictly separated: "
                "previous eval_end must be before next eval_start"
            )
