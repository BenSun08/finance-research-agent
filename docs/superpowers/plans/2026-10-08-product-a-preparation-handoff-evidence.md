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

## Subtask 3: Retain the typed regime result

- RED: three packet tests failed because the builder could not accept a regime
  result. Test setup was corrected to use the frozen configuration's version
  before accepting those failures as RED evidence.
- GREEN: 14 packet tests passed, including complete UNKNOWN/unavailable state,
  wrong-policy/late-result rejection, and legacy byte round trips.
- Full suite: 1,923 passed, one live test deselected, one existing warning.
- Ruff and mypy passed; schema check passed. The absent optional field is excluded
  from serialization to preserve older packet identity and byte counts.

## Prerequisite feature review and coverage

- Independent review of the whole prerequisite scope: review-clean. Reviewer ran
  85 preparation/bridge/packet/replay tests plus populated UNKNOWN and permissive
  regime probes, and compared legacy packet bytes against base `0519c81`.
- Persisted the review's regression recommendations: a base-generated golden hash
  and byte count; known Decimal regime score and 38 metrics; missing/replaced
  metric rejection; late source/bar cutoff; naive/non-UTC cutoff; cutoff identity.
  Reviewer rechecked the test-only diff and independently passed 40 tests.
- Final full suite under branch coverage: 1,931 passed, one live test deselected,
  one existing warning. Ruff/mypy/schema drift/diff checks passed.
- Coverage source is the worktree directory, not pre-imported module names. The
  discarded module-name measurement caused enum/class import interference; its
  failures and coverage were not accepted as verification evidence.

| Module | Statement coverage | Branch coverage |
| --- | ---: | ---: |
| component_versions | 100% (7/7) | No branches |
| run_service | 95.24% (80/84) | 86.67% (26/30) |
| collection_bridge | 92.52% (136/147) | 83.33% (55/66) |
| packet_service | 91.11% (82/90) | 76.47% (26/34) |
| domain/packets | 78.65% (70/89) | 50% (22/44) |

The added regime validation and collected-snapshot projection rejection cases
are covered. Remaining missing branches are existing collection defensive checks,
run reload/window guards, packet trimming paths, and general packet cutoff/size
validation. These figures do not claim 100% coverage or operational preparation.

This feature contains prerequisites only. The typed prepare handoff, shared
deadline, prior observations, stdio executable, plugin/skills and R11 remain.
