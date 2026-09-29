"""Transport-neutral Product A application operation contracts."""

import json
from datetime import UTC, date, datetime
from importlib import import_module
from importlib.util import find_spec

import pytest
from pydantic import ValidationError

EXPECTED_OPERATION_NAMES = (
    "get_system_status",
    "validate_configuration",
    "prepare_premarket_run",
    "get_run_status",
    "get_report",
    "validate_and_publish_brief",
    "publish_reduced_report",
    "list_watchlist",
    "upsert_watchlist_item",
    "remove_watchlist_item",
    "record_run_feedback",
)


def test_product_a_operations_contract_module_is_available() -> None:
    assert find_spec("finance_research_agent.application.operations") is not None


def test_public_operation_allowlist_is_exact_and_ordered() -> None:
    operations = import_module("finance_research_agent.application.operations")

    assert operations.OPERATION_NAMES == EXPECTED_OPERATION_NAMES
    assert tuple(operation.value for operation in operations.ProductAOperation) == (
        EXPECTED_OPERATION_NAMES
    )


def test_each_operation_has_a_typed_request_and_result_contract() -> None:
    operations = import_module("finance_research_agent.application.operations")

    assert hasattr(operations, "OPERATION_CONTRACTS")
    assert tuple(operations.OPERATION_CONTRACTS) == EXPECTED_OPERATION_NAMES
    for name, contract in operations.OPERATION_CONTRACTS.items():
        assert contract.name.value == name
        assert isinstance(contract.request_model, type)
        assert isinstance(contract.result_model, type)


def test_run_status_request_rejects_unknown_fields_and_invalid_identifier() -> None:
    operations = import_module("finance_research_agent.application.operations")

    assert hasattr(operations, "GetRunStatusRequest")
    with pytest.raises(ValidationError):
        operations.GetRunStatusRequest(run_id="premarket-2026-09-29-r1", path="/tmp/run")
    with pytest.raises(ValidationError):
        operations.GetRunStatusRequest(run_id="../outside")


def test_prepare_request_rejects_unbounded_or_caller_controlled_options() -> None:
    operations = import_module("finance_research_agent.application.operations")

    assert hasattr(operations, "PreparePremarketRunOperationRequest")
    request = operations.PreparePremarketRunOperationRequest(
        market_date=date(2026, 9, 29), requested_revision=2
    )
    assert request.market_date == date(2026, 9, 29)
    assert request.requested_revision == 2
    for forbidden in (
        {"provider": "other"},
        {"url": "https://example.com"},
        {"path": "/tmp/run"},
        {"deadline_seconds": 600},
        {"risk_policy": {"max_risk_per_trade_pct": "1"}},
        {"invocation": "SCHEDULED"},
    ):
        with pytest.raises(ValidationError):
            operations.PreparePremarketRunOperationRequest.model_validate(forbidden)
    with pytest.raises(ValidationError):
        operations.PreparePremarketRunOperationRequest(requested_revision=0)


def test_reduced_report_request_uses_only_closed_reasons() -> None:
    operations = import_module("finance_research_agent.application.operations")

    assert hasattr(operations, "PublishReducedReportRequest")
    request = operations.PublishReducedReportRequest(
        run_id="premarket-2026-09-29-r1",
        reason=operations.ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )
    assert request.run_id == "premarket-2026-09-29-r1"
    with pytest.raises(ValidationError):
        operations.PublishReducedReportRequest(
            run_id="premarket-2026-09-29-r1", reason="make_anything_publishable"
        )


def test_operation_parser_accepts_only_registered_names_and_strict_json_arguments() -> None:
    operations = import_module("finance_research_agent.application.operations")

    assert hasattr(operations, "validate_operation_request")
    request = operations.validate_operation_request(
        "prepare_premarket_run",
        json.dumps({"market_date": "2026-09-29", "requested_revision": 2}),
    )
    assert request.market_date == date(2026, 9, 29)
    with pytest.raises(ValueError):
        operations.validate_operation_request("run_anything", "{}")
    with pytest.raises(ValidationError):
        operations.validate_operation_request(
            "get_report", '{"run_id":"premarket-2026-09-29-r1","url":"https://example.com"}'
        )


def test_operation_request_models_are_immutable_strict_and_extra_forbidden() -> None:
    operations = import_module("finance_research_agent.application.operations")

    for contract in operations.OPERATION_CONTRACTS.values():
        assert contract.request_model.model_config["extra"] == "forbid"
        assert contract.request_model.model_config["frozen"] is True
        assert contract.request_model.model_config["strict"] is True


def test_operation_error_is_closed_and_cannot_carry_provider_details() -> None:
    operations = import_module("finance_research_agent.application.operations")

    assert hasattr(operations, "OperationError")
    error = operations.OperationError(code=operations.ErrorCode.INTERNAL_ERROR)
    assert error.code is operations.ErrorCode.INTERNAL_ERROR
    with pytest.raises(ValidationError):
        operations.OperationError(
            code=operations.ErrorCode.INTERNAL_ERROR,
            provider_message="token=secret",
        )


def test_operation_payload_limit_is_enforced_before_json_validation() -> None:
    operations = import_module("finance_research_agent.application.operations")

    with pytest.raises(ValueError, match="maximum payload size"):
        operations.validate_operation_request("list_watchlist", " " * 1_000_001)


def test_operation_parser_rejects_non_string_boundary_values() -> None:
    operations = import_module("finance_research_agent.application.operations")

    with pytest.raises(TypeError):
        operations.validate_operation_request("list_watchlist", {})
    with pytest.raises(TypeError):
        operations.validate_operation_request(None, "{}")


def test_publication_result_requires_exactly_one_typed_outcome() -> None:
    operations = import_module("finance_research_agent.application.operations")
    report = operations.ValidationReport(
        run_id="premarket-2026-09-29-r1",
        packet_id="packet-1",
        packet_sha256="a" * 64,
        is_valid=True,
        repairable=False,
        validation_attempt=1,
        repair_attempts_used=0,
        issues=(),
    )
    artifact = operations.PublishedArtifact(
        run_id="premarket-2026-09-29-r1",
        bundle_sha256="b" * 64,
        markdown_sha256="c" * 64,
        published_at=datetime(2026, 9, 29, tzinfo=UTC),
    )

    assert (
        operations.ValidateAndPublishBriefResult(
            validation_report=report
        ).validation_report
        == report
    )
    assert operations.ValidateAndPublishBriefResult(publication=artifact).publication == artifact
    with pytest.raises(ValidationError):
        operations.ValidateAndPublishBriefResult()
    with pytest.raises(ValidationError):
        operations.ValidateAndPublishBriefResult(validation_report=report, publication=artifact)
