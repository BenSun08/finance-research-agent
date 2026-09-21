from pathlib import Path

from finance_research_agent.adapters.http_client import sanitize_external_text


def test_external_html_is_bounded_sanitized_untrusted_data() -> None:
    raw = Path("tests/fixtures/security/prompt-injection.html").read_bytes()
    result = sanitize_external_text(raw, "text/html", max_chars=120)
    assert len(result.text) <= 120
    assert "<script" not in result.text.lower()
    assert "display:none" not in result.text.lower()
    assert "ignore previous instructions" in result.text.lower()
    assert "hidden style text" not in result.text.lower()
    assert "hidden attribute text" not in result.text.lower()
    assert "\x07" not in result.text
    assert "\u200b" not in result.text
    assert result.source_hash_sha256
    assert result.untrusted is True
