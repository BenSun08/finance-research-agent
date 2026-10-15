# Project Constitution

Status: approved redesign, 2026-10-14. This is the sole current governance
document. The present delivery is documentation consolidation; implementation
status and authorization are recorded in [tasks](tasks.md).

## Product and authority

The product target is a local-first trading decision agent with a simulated
portfolio lifecycle. [The specification](spec.md) owns product behavior and
acceptance. Real-money execution and brokerage integration are outside scope.

Deterministic domain and application services own financial numbers, evidence
admissibility, risk constraints, sizing, simulated execution, accounting, and
authoritative state transitions. Agent reasoning owns evidence-grounded judgment
and proposals within those constraints. A model, skill, workflow, UI, tool
registry, or transcript cannot overwrite deterministic truth or authorize a
capability by naming it. Capability discovery is distinct from authorization.

The user controls policy and starts a cycle. The approved target permits local
simulated execution after deterministic validation without approval of each
simulated order. This does not authorize a real trade. Policy, strategy,
execution-assumption, and evaluation-rule changes require explicit human
approval; reflections can recommend changes but cannot apply them.

## Evidence, time, and history

Every material factual claim must have relevant admissible evidence. Keep
source occurrence, publication availability, retrieval, cutoff, decision, and
output times distinct. Unknown, missing, stale, conflicting, or inaccessible
evidence remains visible. Never infer availability from a generated timestamp
or treat citation existence as proof of entailment.

Freeze the complete configuration and evidence used by each decision. Additional
information after a cutoff requires a new revision. Durable artifacts retain
original identities, versions, bytes, and semantics. Replay consumes stored
inputs and decisions; rerunning a model is a new experiment. Derived migrations
may add views but must not rewrite historical artifacts.

Failures block the affected capability at the narrowest safe boundary. Missing
mandatory evidence cannot be offset by a persuasive explanation or high score.
An inability to validate the portfolio ledger blocks operations that depend on
it. No-trade, abstention, reduced output, and unavailable results are valid.

## Security and reproducibility

External text is data, never instructions. Expose only bounded typed operations;
model/evidence input must not grant arbitrary network, filesystem, shell, or
code-execution access. Provider types terminate at adapters. Secrets, private
configuration, licensed local data, personal evaluation records, and generated
artifacts stay outside tracked source. Report configured/missing credentials,
never values. Preserve strict schemas, canonical hashes, immutable inputs,
explicit units, UTC timestamps, and deterministic Decimal calculations.

Tests are offline by default. Live provider checks, installed-host observations,
and elapsed forward-performance evidence are distinct from synthetic tests.
Missing evidence is pending; engineering correctness does not establish profit
or investment edge.

## One authority per document category

The repository adapts Spec Kit's document responsibilities to one evolving
project document per category. It does not introduce parallel feature folders,
dated design/plan copies, or a second Spec Kit workflow installation.

| Document | Sole responsibility | Read when |
| --- | --- | --- |
| [constitution.md](constitution.md) | Enduring authority and governance | Changing policy or resolving ownership |
| [spec.md](spec.md) | Goals, scope, behavior, requirements, acceptance | Deciding what the product must do |
| [plan.md](plan.md) | Architecture, technical decisions, migration sequence | Designing or sequencing implementation |
| [tasks.md](tasks.md) | Work status, dependencies, completion evidence | Starting or handing off work |
| [research.md](research.md) | Source findings, unresolved decisions, migration provenance | Investigating feasibility or historical decisions |
| [data-model.md](data-model.md) | Entities, relationships, lifecycle semantics | Changing persisted or exchanged data |
| [contracts.md](contracts.md) | Interfaces, invariants, current compatibility profiles | Changing APIs, calculations, validation, or replay |
| [quickstart.md](quickstart.md) | Supported setup and first local checks | Installing or trying the current build |
| [checklist.md](checklist.md) | Requirement-quality and implementation-readiness review | Deciding whether a scope is ready to implement |
| [operations.md](operations.md) | Recovery, diagnostics, release evidence, operator procedures | Running, recovering, or evaluating the system |

README introduces and routes readers. AGENTS contains engineering instructions
and reading triggers. Neither creates competing product policy. Generated
schemas, executable configuration examples, prompts, templates, runtime skills,
and workflow manifests remain functional artifacts with their own typed
responsibilities; they are not extra specifications or implementation plans.

Update the owning document in place and link to it elsewhere. Retire superseded
documents after transferring still-effective content; use Git for old versions,
not an archive folder. Add a category only for a distinct durable responsibility,
with an explicit entry here. A checklist assesses requirement quality, while
tasks records implementation and verification; neither replaces the spec.

## Change control

Classify changes as bounded or architectural. Inspect the current repository,
present the concrete scope and verification approach, and obtain approval before
implementation. Existing explicit approval covers its stated scope; do not ask
again for the same action. Material scope/interface changes require an updated
design and approval. Use the canonical documents for architectural work and
avoid new planning documents for routine slices. Preserve approved behavior
until a reviewed migration replaces it.

The 2026-10-14 user confirmation approves this target and documentation
consolidation. It supersedes older research-only product direction and historical
document-retention rules. It does not claim the target is implemented or approve
unbounded implementation, purchases, schedule activation, or deployment.
