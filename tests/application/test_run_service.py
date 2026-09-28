from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from finance_research_agent.application.run_service import PreparePremarketRunRequest
from finance_research_agent.domain.enums import InvocationType


def test_prepare_request_preserves_explicit_market_date_and_revision() -> None:
    request = PreparePremarketRunRequest(
        market_date=date(2026, 9, 28),
        requested_revision=3,
        invocation=InvocationType.MANUAL,
    )

    assert request.market_date == date(2026, 9, 28)
    assert request.requested_revision == 3
    assert request.invocation is InvocationType.MANUAL

    with pytest.raises(FrozenInstanceError):
        request.requested_revision = 4  # type: ignore[misc]


def test_prepare_request_accepts_unresolved_market_date_and_revision() -> None:
    request = PreparePremarketRunRequest(
        market_date=None,
        requested_revision=None,
        invocation=InvocationType.SCHEDULED,
    )

    assert request.market_date is None
    assert request.requested_revision is None
    assert request.invocation is InvocationType.SCHEDULED


@pytest.mark.parametrize("revision", [0, -1, True, "2"])
def test_prepare_request_rejects_invalid_requested_revision(revision) -> None:
    with pytest.raises((TypeError, ValueError)):
        PreparePremarketRunRequest(
            market_date=None,
            requested_revision=revision,
            invocation=InvocationType.SCHEDULED,
        )


@pytest.mark.parametrize(
    ("market_date", "invocation"),
    [
        (datetime(2026, 9, 28), InvocationType.MANUAL),
        (None, "MANUAL"),
    ],
)
def test_prepare_request_rejects_non_contract_field_types(market_date, invocation) -> None:
    with pytest.raises(TypeError):
        PreparePremarketRunRequest(
            market_date=market_date,
            requested_revision=None,
            invocation=invocation,
        )
