from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from finance_research_agent.adapters.shadow_evidence import (
    FileSystemOperatorReleaseAttestationRepository,
    FileSystemShadowObservationRepository,
)
from finance_research_agent.domain.enums import Capability
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    OperatorReleaseCheckId,
    OperatorReleaseResult,
    ShadowDayAvailability,
    ShadowDayObservation,
    ShadowExecutiveEventState,
    ShadowExecutiveIdentification,
)


class _Runs:
    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()

    def load(self, run_id: str):
        return object() if run_id in self.known else None


class _Feedback:
    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()

    def list_feedback(self):
        return tuple(SimpleNamespace(feedback_id=item) for item in sorted(self.known))


def _observation(observation_id: str = "shadow-day-1") -> ShadowDayObservation:
    return ShadowDayObservation(
        id=observation_id,
        market_date=date(2026, 10, 9),
        recorded_at=datetime(2026, 10, 10, 0, 0, tzinfo=UTC),
        session_id="session-2026-10-09",
        availability=ShadowDayAvailability.ONLINE,
        observed_run_ids=("premarket-2026-10-09-r1",),
        observed_feedback_ids=("feedback-1",),
        executive_identification=ShadowExecutiveIdentification(
            market_posture=Regime.NEUTRAL,
            event_state=ShadowExecutiveEventState.NO_MATERIAL_EVENTS,
            event_ids=(),
            priority_symbols=("AAPL",),
            disabled_capabilities=(Capability.EVENT_RISK_CHECK_AVAILABLE,),
        ),
    )


def _attestation() -> OperatorReleaseAttestation:
    return OperatorReleaseAttestation(
        id="attestation-desktop-1",
        check_id=OperatorReleaseCheckId.DESKTOP_INSTALL,
        result=OperatorReleaseResult.PASS,
        operator_id="operator-1",
        recorded_at=datetime(2026, 10, 10, 1, 0, tzinfo=UTC),
        package_version="0.5.0.dev0",
        skill_version="sha256:" + "b" * 64,
        source_commit_sha256="c" * 64,
        source_tree_sha256="d" * 64,
        evidence_sha256="e" * 64,
    )


def test_observation_repository_writes_canonical_bytes_and_resolves_trusted_refs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root,
        run_repository=_Runs({"premarket-2026-10-09-r1"}),
        feedback_repository=_Feedback({"feedback-1"}),
    )
    record = _observation()

    repository.append_observation(record)

    path = root / "shadow-day-1.json"
    assert path.read_bytes() == canonical_bytes(record)
    assert repository.get_observation(record.id) == record
    assert repository.get_observation("shadow-day-missing") is None
    assert repository.list_observations() == (record,)
    assert repository.has_run_id("premarket-2026-10-09-r1") is True
    assert repository.has_run_id("premarket-2026-10-09-r2") is False
    assert repository.has_feedback_id("feedback-1") is True
    assert repository.has_feedback_id("feedback-2") is False


def test_observation_repository_never_overwrites_an_existing_id(tmp_path: Path) -> None:
    repository = FileSystemShadowObservationRepository(
        tmp_path / "shadow-days", run_repository=_Runs(), feedback_repository=_Feedback()
    )
    first = _observation()
    repository.append_observation(first)

    with pytest.raises(FileExistsError):
        repository.append_observation(_observation().model_copy(update={"session_id": "changed"}))

    assert repository.get_observation(first.id) == first


def test_observation_repository_rejects_noncanonical_and_malformed_files(tmp_path: Path) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )
    record = _observation()
    root.mkdir()
    (root / "wrong-name.json").write_bytes(canonical_bytes(record))
    with pytest.raises(ValueError, match="filename does not match"):
        repository.list_observations()
    (root / "wrong-name.json").unlink()

    (root / f"{record.id}.json").write_bytes(b" " + canonical_bytes(record))

    with pytest.raises(ValueError, match="not in canonical form"):
        repository.list_observations()

    (root / f"{record.id}.json").write_text("{not json}", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        repository.get_observation(record.id)


def test_observation_repository_rejects_symlink_root_and_record(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    linked_root = FileSystemShadowObservationRepository(
        root_link, run_repository=_Runs(), feedback_repository=_Feedback()
    )
    with pytest.raises(ValueError, match="root must be a local directory"):
        linked_root.list_observations()

    broken_root_link = tmp_path / "broken-root-link"
    broken_root_link.symlink_to(tmp_path / "absent-directory", target_is_directory=True)
    broken_linked_root = FileSystemShadowObservationRepository(
        broken_root_link,
        run_repository=_Runs(),
        feedback_repository=_Feedback(),
    )
    with pytest.raises(ValueError, match="root must be a local directory"):
        broken_linked_root.list_observations()

    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")
    file_root_repository = FileSystemShadowObservationRepository(
        root_file,
        run_repository=_Runs(),
        feedback_repository=_Feedback(),
    )
    with pytest.raises(ValueError, match="root must be a local directory"):
        file_root_repository.list_observations()

    root = tmp_path / "shadow-days"
    root.mkdir()
    (root / "shadow-day-1.json").symlink_to(tmp_path / "absent.json")
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )
    with pytest.raises(ValueError, match="regular local file"):
        repository.get_observation("shadow-day-1")


def test_observation_lookup_rejects_identifiers_that_escape_its_root(tmp_path: Path) -> None:
    root = tmp_path / "shadow-days"
    root.mkdir()
    record = _observation()
    (tmp_path / f"{record.id}.json").write_bytes(canonical_bytes(record))
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )

    with pytest.raises(ValueError, match="identifier is invalid"):
        repository.get_observation(f"../{record.id}")


def test_operator_attestations_are_immutable_and_filename_bound(tmp_path: Path) -> None:
    repository = FileSystemOperatorReleaseAttestationRepository(tmp_path / "attestations")
    attestation = _attestation()
    repository.append_attestation(attestation)

    path = tmp_path / "attestations" / f"{attestation.id}.json"
    assert path.read_bytes() == canonical_bytes(attestation)
    assert repository.list_attestations() == (attestation,)
    assert sha256(path.read_bytes()).hexdigest() == sha256(canonical_bytes(attestation)).hexdigest()
    with pytest.raises(FileExistsError):
        repository.append_attestation(
            attestation.model_copy(update={"operator_id": "different-operator"})
        )


def test_repositories_return_empty_when_fixed_data_directories_do_not_exist(
    tmp_path: Path,
) -> None:
    observations = FileSystemShadowObservationRepository(
        tmp_path / "missing-days", run_repository=_Runs(), feedback_repository=_Feedback()
    )
    attestations = FileSystemOperatorReleaseAttestationRepository(tmp_path / "missing-attestations")

    assert observations.list_observations() == ()
    assert observations.get_observation("shadow-day-1") is None
    assert attestations.list_attestations() == ()


def test_failed_atomic_file_creation_leaves_no_record_or_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )

    def fail_before_temp_file(*args: object, **kwargs: object) -> None:
        raise PermissionError("fixture cannot create a temp file")

    with monkeypatch.context() as context:
        context.setattr(
            "finance_research_agent.adapters.shadow_evidence.tempfile.mkstemp",
            fail_before_temp_file,
        )
        with pytest.raises(OSError, match="could not append shadow observation"):
            repository.append_observation(_observation())

    assert list(root.iterdir()) == []


def test_failed_exclusive_link_cleans_up_the_staged_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )

    def fail_link(source: object, destination: object) -> None:
        raise PermissionError("fixture cannot link the record")

    with monkeypatch.context() as context:
        context.setattr(
            "finance_research_agent.adapters.shadow_evidence.os.link", fail_link
        )
        with pytest.raises(OSError, match="could not append shadow observation"):
            repository.append_observation(_observation())

    assert list(root.iterdir()) == []
