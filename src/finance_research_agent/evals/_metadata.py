"""One canonical token rule for deterministic evaluation metadata."""

from re import fullmatch


def require_canonical_token(value: str, field_name: str) -> None:
    """Reject rather than normalize lowercase ASCII tokens with single separators."""

    if not isinstance(value, str) or fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*", value) is None:
        raise ValueError(
            f"{field_name} must be a canonical lowercase ASCII token "
            "with optional underscore or hyphen separators"
        )
