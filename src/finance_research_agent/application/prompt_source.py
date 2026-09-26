"""Read the single canonical synthesis prompt and its independent digest."""

from hashlib import sha256
from pathlib import Path

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "research-brief-draft.md"


def load_canonical_prompt() -> bytes:
    """Return exact tracked prompt bytes without normalization or duplication."""
    return _PROMPT_PATH.read_bytes()


def canonical_prompt_sha256() -> str:
    """Hash the canonical prompt source independently of package version labels."""
    return sha256(load_canonical_prompt()).hexdigest()
