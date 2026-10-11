"""Record one operator release-check attestation in the configured private store."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from finance_research_agent.adapters.shadow_evidence import (
    FileSystemOperatorReleaseAttestationRepository,
)
from finance_research_agent.application.shadow_evidence_service import (
    RecordOperatorReleaseAttestation,
    record_operator_release_attestation,
)
from scripts._private_shadow_evidence import (
    private_evidence_roots,
    read_strict_request,
    write_receipt,
)


class _SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.parse_args(argv)
    try:
        request = read_strict_request(RecordOperatorReleaseAttestation)
        _, record_root = private_evidence_roots("operator-attestations")
        receipt = record_operator_release_attestation(
            request,
            FileSystemOperatorReleaseAttestationRepository(record_root),
            _SystemClock(),
        )
    except Exception:
        print(
            "Operator attestation rejected: invalid request or private storage unavailable",
            file=sys.stderr,
        )
        return 1
    write_receipt(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
