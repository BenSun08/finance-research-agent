from importlib import import_module


def test_finance_research_agent_is_importable() -> None:
    module = import_module("finance_research_agent")

    assert module.__name__ == "finance_research_agent"
