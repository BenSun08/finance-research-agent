# Product A Skill and Workflow Contract Architecture Delta

**Document status:** Approved architecture delta

**Approval date:** 2026-08-31

**Baseline specification:**
[`2026-08-19-ai-market-research-agent-premarket-design.md`](2026-08-19-ai-market-research-agent-premarket-design.md)

**Baseline implementation blueprint:**
[`2026-08-20-ai-market-research-agent-premarket-v0.1.md`](../plans/2026-08-20-ai-market-research-agent-premarket-v0.1.md)

**Reference architecture reviewed:**
[`tradermonty/claude-trading-skills`](https://github.com/tradermonty/claude-trading-skills)

## 1. Decision and Scope

This document records a small, approved delta to the existing Product A
architecture. It does not replace or restate the Product A specification. The
approved baseline remains authoritative except where this document narrows the
future skill and workflow contract work described in Tasks 17 and 18 of the
master implementation blueprint.

The delta applies only to:

1. skill contracts;
2. workflow contracts and manifests;
3. canonical metadata and source-of-truth hierarchy;
4. skill and workflow verification lifecycle;
5. progressive loading of `SKILL.md`, references, and scripts; and
6. documentation drift prevention.

`ADOPT NOW` in this document means “adopt as an approved Product A architecture
decision for the future Tasks 17–18 slice.” It does not authorize implementation
during the current v0.2 Data Layer milestone. No MCP, scheduler, SEC/macro,
portfolio-risk, language-model integration, brokerage, or execution capability
is authorized by this delta.

## 2. Preserved Product A Baseline

The following baseline decisions are unchanged:

- Product A is local-first, personal, research-only decision-support software.
- Codex is the initial synthesis runtime.
- A narrow stdio MCP surface is the future runtime boundary between Codex and
  the application; skills do not call providers directly.
- The provider-independent Python domain owns normalized data, calculations,
  metrics, scores, levels, sizing, gates, and state transitions.
- Codex may explain deterministic results but may not invent, recalculate, or
  alter numeric truth.
- Evidence and configuration are frozen per run or revision, and replay never
  refreshes network data.
- Missing, stale, conflicting, unauthorized, or invalid inputs fail closed at
  the affected capability.
- Human review and approval remain mandatory. The system has no brokerage,
  account, position, buying-power, order, routing, cancellation, or execution
  capability.
- Product A scope remains fixed. This delta does not introduce holdings,
  portfolio heat, whole-market scanning, brokerage integration, or another
  product.

## 3. Reference-Pattern Classification

The reference repository demonstrates useful contract discipline at a much
larger scale: minimal `SKILL.md` frontmatter, canonical workflow manifests,
cross-reference validation, progressive reference loading, verification axes,
generated catalogs, skill packages, skillsets, and bilingual documentation.
Product A adopts only the parts justified by its smaller scope.

| Reference pattern | Classification | Evidence | Product A trade-off and decision |
|---|---|---|---|
| Minimal `SKILL.md` frontmatter with `name` and `description` | **ADOPT NOW** | Reference skills expose discovery metadata in two frontmatter fields. | Sufficient for Codex discovery and avoids a second metadata authority. Product A keeps exactly these two keys. |
| Explicit purpose, trigger, prerequisites, workflow, output, failure, and reference-loading guidance inside a skill | **ADOPT NOW** | Reference skills make execution and “when to load references” explicit. | Product A standardizes a smaller set of mandatory body sections so safety and failure semantics are reviewable without a registry. |
| Machine-readable ordered workflow manifests | **ADOPT NOW** | `workflows/*.yaml` is canonical for multi-skill ordering, artifacts, and gates in the reference repository. | Product A needs one manifest for its complex premarket state machine. The manifest is adjacent to that skill, not a repository-wide workflow platform. |
| Versioned, additive manifest schema | **ADOPT NOW** | The reference metadata and workflow schemas use `schema_version: 1` and additive evolution. | A version field makes drift detectable. Existing field meanings must not be repurposed. |
| Declared `consumes` and `produces` handoffs | **ADOPT NOW** | Reference workflow steps declare artifact flow and validators reject invalid backward references. | This makes Product A ordering and frozen-packet handoffs statically checkable without implementing a workflow engine. |
| Explicit human or fail-closed decision gates | **ADOPT NOW** | Reference workflows distinguish decision gates and manual review from mechanical execution. | Product A uses terminal `published`, `deterministic_reduced`, or `blocked` outcomes and never treats publication as trade approval. |
| Validator-enforced name, path, dependency, and workflow consistency | **ADOPT NOW** | The reference validator checks directory/frontmatter parity, workflow IDs, step dependencies, and artifact flow. | Product A implements focused contract tests and one documentation checker, not a generalized registry validator. |
| Separate lifecycle status and verification evidence | **ADOPT NOW** | The reference repository warns that a `production` label is not proof and records verification across several evidence axes. | Product A adopts the principle in a smaller form: release claims require static contracts, protocol tests, replay, security checks, and a smoke gate. It does not add self-reported verification metadata. |
| Progressive loading from discovery metadata to `SKILL.md`, then references and scripts | **ADOPT NOW** | Reference skills defer methodology references until needed and keep executable helpers in skill folders. | Product A adapts staged loading to its existing boundary: financial/data code stays in the deterministic package behind the future MCP/core boundary. Skill-local financial scripts are prohibited. |
| Documentation checked against canonical contracts | **ADOPT NOW** | The reference repository validates and generates downstream documentation from canonical metadata and manifests. | Product A adds link, manifest, operation-allowlist, source-hierarchy, and no-execution drift checks. It does not build a documentation generator. |
| Stable validator error-code catalog | **ADOPT LATER** | The reference workflow validator publishes stable `IDX` and `WF` codes. | Product A initially has one complex manifest and local CI consumers. Stable public codes become justified only if other tools or repositories consume the validator. |
| Central `skills-index.yaml` registry | **DO NOT ADOPT** | The reference repository uses an authoritative index for dozens of skills, integrations, roles, and workflow back-references. | Product A has three bounded skills. A registry would duplicate frontmatter, paths, and contracts without demonstrated scale. |
| Registry-owned lifecycle status, operational roles, and `knowledge_only` markers | **DO NOT ADOPT** | The reference index classifies production/beta state, workflow roles, and script-free exemptions. | Product A has no catalog lifecycle or role-selection problem. Tests verify the concrete contracts directly. |
| Verification baseline YAML mirroring every skill | **DO NOT ADOPT** | The reference repository maintains a separate production-verification baseline with parity tests. | Product A records evidence in tests, replay fixtures, CI results, and release review. A mirrored status file would create another drift surface. |
| Purpose-specific `skillsets/` and a Navigator | **DO NOT ADOPT** | The reference repository groups a large catalog for user discovery and anticipates a Navigator. | Product A has one product workflow and no skill-selection problem. Selection remains explicit. |
| Required/optional skill bundles and API profiles | **DO NOT ADOPT** | Reference workflows declare installed-skill sets and data-access profiles for many interchangeable routines. | Product A has one fixed premarket skill and one approved MCP surface. Optional skill selection and provider-profile routing would weaken the fixed boundary. |
| Cross-workflow prerequisite and downstream-hint graph | **DO NOT ADOPT** | The reference repository records informational dependencies between several workflows. | Product A has one complex workflow, so a cross-workflow graph has no consumer and would be speculative metadata. |
| Pre-built `.skill` package distribution | **DO NOT ADOPT** | The reference repository builds uploadable packages for a broad user base. | Product A is a personal local plugin and public marketplace distribution is outside scope. |
| Bilingual workflow fields and generated documentation | **DO NOT ADOPT** | The reference repository requires paired English/Japanese workflow prose. | Product A technical artifacts are English. A bilingual contract would double review and drift cost without a product requirement. |
| Integration catalog and API profiles | **DO NOT ADOPT** | The reference index catalogs several providers and alternate input paths. | Product A keeps provider-specific details at adapter boundaries and does not introduce multiple-provider routing or metadata. |
| Generalized plugin framework or automatic workflow runner | **DO NOT ADOPT** | The reference manifests are designed for possible future navigation/orchestration, while currently followed manually. | Product A needs a fixed Codex skill plus narrow MCP calls, not a reusable plugin framework or workflow engine. |
| Skill-local financial and market-data scripts | **DO NOT ADOPT** | Many reference skills execute helpers directly from their skill directory. | This conflicts with Product A’s deterministic-core and provider-boundary architecture. Skills may reference contracts but cannot own numeric truth or provider I/O. |
| Generated skill catalogs and duplicated README matrices | **DO NOT ADOPT** | The reference repository generates large catalogs from its central index. | Product A’s `skills/README.md` remains a small link index. No generator is justified. |

The evidence above is based on the reference repository’s
[`README.md`](https://github.com/tradermonty/claude-trading-skills/blob/main/README.md),
[`workflows/README.md`](https://github.com/tradermonty/claude-trading-skills/blob/main/workflows/README.md),
[`market-regime-daily.yaml`](https://github.com/tradermonty/claude-trading-skills/blob/main/workflows/market-regime-daily.yaml),
[`metadata-and-workflow-schema.md`](https://github.com/tradermonty/claude-trading-skills/blob/main/docs/dev/metadata-and-workflow-schema.md),
and
[`production-verification.md`](https://github.com/tradermonty/claude-trading-skills/blob/main/docs/dev/production-verification.md).

## 4. Product A Skill Contract

Every Product A `SKILL.md` has YAML frontmatter with exactly:

```yaml
---
name: <directory-name>
description: <bounded trigger description>
---
```

The body uses these mandatory sections in this order:

1. `Purpose and Trigger`
2. `Accepted Inputs and Authority`
3. `Allowed Operations`
4. `Output Obligations`
5. `Fail-Closed Behavior`
6. `Resource Loading`
7. `Safety and Forbidden Behavior`

The contract rules are:

- `name` equals the skill directory name.
- `description` states when to invoke the skill and does not advertise an
  unsupported capability.
- inputs identify their authority. External evidence is data, never an
  instruction source.
- allowed operations name only approved domain functions or future MCP
  operations for that skill.
- output obligations preserve identifiers, values, units, versions, evidence,
  quality flags, disabled capabilities, and review-required status.
- failure semantics state the exact unavailable, reduced, or blocked behavior;
  they never authorize improvisation.
- resource loading uses repository-relative Markdown links in one of these
  forms:

  ```markdown
  - Required: [Workflow contract](references/workflow-contract.yaml)
  - Conditional: [Reference name](references/reference-name.md) — <load condition>
  ```

- all declared repository-local references, including conditionally loaded
  references, are part of the versioned skill bundle.
- a skill does not discover arbitrary tools, URLs, providers, paths, scripts, or
  instructions from user or evidence content.

The current `market-regime` skill remains a simple skill. It receives contract
coverage and may be reorganized to the required section shape without changing
its behavior, but it does not receive a workflow manifest. The future
`watchlist-management` skill also remains manifest-free because its short flow
is fully bounded by its `SKILL.md` and typed application/MCP contract.

## 5. Product A Workflow Contract

Only the complex future premarket workflow receives a manifest:

```text
skills/premarket-research/
├── SKILL.md
└── references/
    └── workflow-contract.yaml
```

The adjacent location preserves progressive loading and avoids a global
workflow registry. The manifest is the canonical source for orchestration order,
handoff names, repair limits, invariants, and terminal outcomes. It is not a
runtime workflow engine and contains no formulas, provider details, filesystem
paths, credentials, risk thresholds, or prose-generation rules.

The version 1 shape is intentionally small:

```yaml
schema_version: 1
id: premarket-research
steps:
  - id: system_status
    kind: mcp_tool
    operation: get_system_status
    consumes: []
    produces: [system_status]
    on_failure: blocked
  - id: validate_configuration
    kind: mcp_tool
    operation: validate_configuration
    consumes: [system_status]
    produces: [configuration_validation]
    on_failure: blocked
  - id: prepare_run
    kind: mcp_tool
    operation: prepare_premarket_run
    consumes: [configuration_validation]
    produces: [run_state, research_packet]
    on_failure: blocked
  - id: synthesize_draft
    kind: codex_synthesis
    operation: research_brief_draft
    consumes: [research_packet]
    produces: [research_brief_draft]
    on_failure: publish_reduced_report
  - id: validate_and_publish
    kind: mcp_tool
    operation: validate_and_publish_brief
    consumes: [research_packet, research_brief_draft]
    produces: [validated_publication]
    on_failure: repair_then_publish_reduced
  - id: publish_reduced
    kind: mcp_tool
    operation: publish_reduced_report
    consumes: [run_state]
    produces: [reduced_publication]
    on_failure: blocked
  - id: read_report
    kind: mcp_tool
    operation: get_report
    consumes: [run_state]
    produces: [report]
    on_failure: blocked
repair:
  max_repairs: 2
  max_validations: 3
  research_packet: same_frozen_packet
invariants:
  - no_new_evidence_after_cutoff
  - no_numeric_recalculation_in_synthesis
  - no_policy_mutation
  - no_tool_discovery
  - no_brokerage_or_execution
terminal_outcomes:
  - published
  - deterministic_reduced
  - blocked
```

The manifest references the operation and artifact names defined by the
approved Product A application/MCP contracts. It must not create aliases for
those names. Branch semantics that depend on typed runtime status remain in the
typed application contract and the `SKILL.md`; the manifest does not duplicate
the run-state model.

Schema evolution is additive. A future meaning change requires a new schema
version; an existing field is never silently repurposed.

## 6. Canonical Source-of-Truth Hierarchy

When two artifacts disagree, the higher authority in this table wins. The lower
artifact must be corrected; it must not override the higher artifact.

| Priority | Authority | Owns | Must not own |
|---:|---|---|---|
| 1 | Approved Product A specification plus approved architecture deltas | Product scope, safety boundaries, human gates, component responsibilities | Runtime values or implementation sequencing |
| 2 | Python typed domain models, policies, calculations, and validators | Numeric truth, states, units, gates, deterministic semantics | Codex discovery prose or provider response shapes |
| 3 | Generated JSON Schemas derived from typed models | Serialized input/output contract | Independently edited business rules |
| 4 | `workflow-contract.yaml` | Premarket orchestration order, handoffs, repair limits, invariants, terminal outcomes | Formulas, providers, paths, credentials, risk thresholds |
| 5 | `SKILL.md` | Discovery, trigger semantics, loading rules, allowed operations, output/failure/safety behavior | Numeric calculation or a competing workflow definition |
| 6 | Canonical synthesis prompt source | ResearchPacket-to-ResearchBriefDraft instructions | Numeric truth or workflow ordering |
| 7 | Canonical report template | Deterministic Markdown rendering | Run truth or new factual content |
| 8 | Immutable JSON run bundle | The canonical truth for one frozen run and its recorded component hashes | Global product policy |
| 9 | README, scheduling prompt, rendered Markdown, and explanatory docs | Human navigation and derived presentation | Canonical workflow order or numeric truth |
| 10 | Master implementation blueprint | Sequencing, dependencies, and review checkpoints | Runtime truth or authorization to execute all milestones end-to-end |

The ordering above governs authoring and interpretation within one contract
revision. It does not rewrite history: for an already published run, that run's
immutable JSON bundle and the exact component versions recorded inside it remain
canonical. A newer specification, model, manifest, skill, prompt, or template
applies only to a new run or revision. Replay either uses the recorded compatible
versions or fails closed on a version mismatch.

The hierarchy is narrow by design. Product A does not add a skill registry,
metadata catalog, provider catalog, or generated documentation database.

## 7. Skill Workflow Version

The existing `skill_version` run field becomes the content digest of the loaded
skill bundle; no manual semantic version or registry entry is added.

For a skill root, the bundle contains:

1. `SKILL.md`; and
2. every repository-local resource declared under `Resource Loading`, including
   conditional resources.

Logical paths are POSIX paths relative to the skill root. Absolute paths,
backslashes, `.` segments, `..` segments, symlinks, duplicate logical paths, and
resources outside the skill root are rejected.

The digest is SHA-256 over files sorted by the UTF-8 bytes of their logical
paths. Each entry is framed to avoid concatenation ambiguity:

```text
uint64_be(path_byte_length)
path_utf8_bytes
uint64_be(content_byte_length)
exact_file_bytes
```

The stored value is `sha256:<lowercase-hex>`. Any byte change to `SKILL.md` or a
declared resource changes `skill_version` and invalidates prior verification
evidence for the changed contract. Prompt and report-template bytes remain under
their separate `prompt_version` and `report_template_version` fields.

## 8. Verification Lifecycle

Product A does not infer readiness from a label. A skill/workflow change passes
through four evidence layers:

1. **Static contract tests**
   - frontmatter has exactly `name` and `description`;
   - directory and `name` match;
   - mandatory body sections exist in order;
   - declared resources exist inside the skill root;
   - manifest schema version, ID, keys, step IDs, artifact dependencies,
     operations, repair limits, invariants, and outcomes are exact;
   - every MCP operation is in the approved Product A allowlist; and
   - simple skills do not acquire manifests without a separately approved need.
2. **Fake protocol tests**
   - typed fake MCP results exercise happy publication, already-published read,
     operational failure, zero-repair success, one-repair success, two-repair
     success or exhaustion, synthesis timeout, deterministic reduced
     publication, unauthorized-operation rejection, and post-cutoff evidence
     rejection;
   - every validation uses the same frozen `ResearchPacket`; and
   - no test performs network access or uses credentials.
3. **Frozen replay**
   - recorded input, manifest, skill bundle, prompt, schemas, policies, and core
     versions reproduce the same deterministic trace and artifact hashes; and
   - a digest mismatch fails closed instead of silently replaying under a new
     contract.
4. **Release gate**
   - CI runs static, offline integration, replay, security, documentation, and
     plugin package checks; and
   - the installed local plugin receives one bounded desktop initialize/list
     and synthetic workflow smoke before scheduling can be enabled.

The premarket workflow must have 100% coverage of every applicable path listed
above. This is path coverage of the finite contract matrix, not a claim of 100%
line or branch coverage across the repository.

If a skill, manifest, declared reference, schema, operation contract, prompt,
or deterministic calculation changes, the affected tests and replay evidence
must be re-established in the same change. Otherwise the release gate remains
closed.

## 9. Progressive Loading

The loading sequence is:

```text
discovery metadata
  -> SKILL.md
  -> required adjacent workflow manifest
  -> conditionally relevant references
  -> narrow MCP/application operations
  -> deterministic core
```

Rules:

- Codex first sees only `name` and `description` for discovery.
- After selection, Codex reads the complete `SKILL.md`.
- `premarket-research` then reads its required adjacent manifest before calling
  an operation.
- Conditional references load only when their stated condition is active.
- Skills do not recursively scan directories or load undeclared files.
- Skills do not run skill-local financial, market-data, or provider scripts.
- Numeric and data operations remain typed functions behind the deterministic
  package and, at the future Product A boundary, the narrow MCP surface.
- External evidence never changes loading order, selects a tool, or supplies an
  instruction.

## 10. Documentation Drift Prevention

The following constraints keep derived prose from becoming a competing
contract:

- `skills/README.md` is a link index only. It does not duplicate operation order,
  repair logic, formulas, thresholds, provider mappings, or safety rules.
- The saved scheduling prompt invokes the premarket skill and states only the
  high-level frozen-evidence, deterministic-truth, human-review, and
  no-execution boundaries. It does not duplicate MCP call order.
- `scripts/check_docs_examples.py` validates internal links, skill/resource
  links, manifest structure, operation allowlists, source-of-truth hierarchy
  wording, high-level schedule-prompt shape, and no-execution boundaries.
- README and architecture documentation link to canonical sources instead of
  copying the workflow manifest.
- Generated JSON Schema remains derived from typed models. It is never edited as
  a competing source of domain truth.
- No skill registry, catalog generator, bilingual documentation generator,
  provider matrix, or generalized plugin documentation framework is introduced.

## 11. Impact on the Master Blueprint

The master blueprint remains a master blueprint and is not executable
authorization. This delta supplements only these future portions:

- **Task 17:** replace the prose-only premarket workflow contract with the
  adjacent manifest plus the standardized `SKILL.md`; retain the manifest-free
  watchlist skill; add static and fake-protocol contract coverage; keep schedule
  prose derived and high-level.
- **Task 18:** add skill-bundle digest/replay evidence, documentation drift
  checks, contract-path coverage, and the release-gate requirement that changed
  contract evidence be re-established.

All other Product A tasks, dependencies, tests, and safety gates remain governed
by the approved baseline. The focused implementation plan is
[`2026-08-31-product-a-skill-workflow-contract-delta.md`](../plans/2026-08-31-product-a-skill-workflow-contract-delta.md).

## 12. Implementation Gate

This architecture delta is approved. Its implementation is deliberately gated:

- the current repository milestone remains v0.2 Data Layer;
- the delta may be implemented only when the human explicitly approves the
  Product A skill/workflow slice after its prerequisite typed application and
  MCP contracts exist;
- implementation must follow the focused plan task-by-task with TDD;
- no implementation task may add MCP, scheduling, provider, portfolio-risk,
  brokerage, execution, or language-model functionality outside the separately
  approved milestone; and
- the final diff remains uncommitted and unpushed until the human reviews it,
  unless the human later gives explicit commit or push authorization.
