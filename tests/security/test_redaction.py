from finance_research_agent.adapters.http_client import redact


def test_headers_and_credential_shaped_values_are_redacted() -> None:
    raw = "Authorization: Bearer secret-token ALPACA_API_KEY=abc123"
    cleaned = redact(raw, secrets=("secret-token", "abc123"))
    assert "secret-token" not in cleaned
    assert "abc123" not in cleaned
    assert "[REDACTED]" in cleaned
