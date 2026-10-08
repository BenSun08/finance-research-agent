# Product A Preparation Handoff Evidence

Base: `0519c8126cde4e15343821877a7c87837ea25fa9` (merged PR #65).
Design: `../specs/2026-10-08-product-a-preparation-handoff-correction.md`.

## Subtask 1: Trusted frozen component versions

- RED: preparation tests failed for the placeholder prompt digest and missing
  trusted version callback; the next run exposed the 64-character version bound
  rejecting the approved 71-character skill digest.
- GREEN: 31 focused preparation/dependencies/packet tests passed. Malformed skill
  digests fail before allocation; a resumed revision does not consult new versions.
- Full suite: 1,915 passed, one live test deselected, one existing websockets warning.
- Ruff passed; mypy passed (87 source files); final diff check passed.
- Schema regeneration initially used the shared virtualenv's older installed
  checkout and caused the schema drift test to fail. Regenerated with explicit
  `PYTHONPATH=src`; full suite then passed. Only the five schema exports containing
  the changed skill version field remain changed.
- Whole-feature independent review and coverage remain pending. This subtask
  does not claim an operational prepare handoff.

## Subtask 2: Collected snapshot to regime projection

- RED: four tests failed because the collection-compatible projection was absent.
- GREEN: 24 collection/historical bridge tests passed, including numeric/evidence
  fidelity, changed-content identity, and empty/late/inconsistent input rejection.
- Full suite: 1,919 passed, one live test deselected, one existing warning.
- Ruff passed after test wrapping; mypy passed (87 source files). The legacy
  historical bridge remains unchanged.
