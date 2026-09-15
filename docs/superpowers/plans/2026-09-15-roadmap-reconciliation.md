# Roadmap Reconciliation Documentation Implementation Plan

Date: 2026-09-15

Scope: Documentation and architecture-roadmap reconciliation only
Design: [Product A Roadmap Reconciliation Design](../specs/2026-09-15-roadmap-reconciliation-design.md)

## Purpose

Align forward-facing repository guidance with the verified v0.1→v0.5
implementation and the approved Product A authority hierarchy. This is a
documentation implementation plan, not a new Product A implementation plan.

The verified base is PR #22's merge commit,
`9583698e07b27043e2a0e37279d6b1e55bf09fc5`. If `main` advances, inspect the
new commits before applying this plan and preserve newer intentional changes.

## Scope Boundaries

- Do not modify historical plans.
- Do not add Product A source code.
- Do not add or modify AgentRuntime behavior.
- Do not add MCP.
- Do not add model SDKs.
- Do not add `skills-index.yaml`.
- Do not add a generalized workflow engine.
- Do not add provider adapters.
- Do not add financial calculations or policy.
- Do not add brokerage/trading functionality.
- Do not create new skills or workflow manifests.

Expected changed files are exactly:

- `docs/superpowers/specs/2026-09-15-roadmap-reconciliation-design.md`
- `docs/superpowers/plans/2026-09-15-roadmap-reconciliation.md`
- `README.md`
- `AGENTS.md`

## 1. Add the Reconciliation Design

Create the dated reconciliation specification without altering the historical
Product A specification or master plans.

The design must:

- record the verified v0.1–v0.5 implementation state;
- accept that implementation path as a valid foundation;
- preserve Product A as the primary product goal;
- limit `AgentRuntime` to non-authoritative generic model/tool orchestration;
- define the Product A workflow authority hierarchy;
- preserve `Tool registered != Tool authorized != Workflow permits execution`;
- selectively adopt useful skill/workflow patterns from the reference repository;
- document why the current repository does not need `skills-index.yaml` or a
  generalized workflow engine;
- sequence Product A typed application/evidence work before skills/workflows,
  MCP, a production model adapter, and shadow-mode automation; and
- list acceptance criteria, non-goals, deferred work, curriculum alignment, and
  documentation source-of-truth policy.

## 2. Align README

Retain useful v0.5 descriptions of `ModelPort`, `ModelResponse`,
`AssistantAction`, `ToolCall`, `ToolRegistry`, `ToolPort`, `ToolObservation`, and
`AgentRuntime`.

Replace the simplistic `v0.6 MCP` / `v0.7 Automation` roadmap with:

1. Product A deterministic application/evidence contracts;
2. Product A skills and a declarative workflow;
3. MCP over approved typed operations;
4. a production model provider adapter; and
5. shadow-mode automation, evaluation, and learning.

Add the reconciliation-design link and explicitly state that `AgentRuntime` owns
only the generic bounded model/tool loop, not Product A financial policy,
evidence authority, publication validity, approval state, or workflow semantics.

## 3. Align AGENTS

Record v0.5 through Slice 3B as a completed foundation and set the current
forward direction to Product A deterministic application/evidence contracts.

Add the Product A workflow authority hierarchy and explicit constraints against:

- using `AgentRuntime` as the authoritative Product A workflow engine;
- moving deterministic financial policy into prompts or model decisions;
- letting model reasoning define freshness or source authority;
- letting a model alter scores, sizing, gates, or state transitions;
- treating registry membership as authorization; and
- adding production model integration before Product A typed prerequisites are
  stable and separately approved.

Keep all existing no-trading, no-brokerage, no-MCP, no-scheduling, and bounded
runtime protections. Keep the package development version at `0.5.0.dev0`.

## 4. Inspect Skills, Manifests, and Stale Claims

Inventory `skills/` and every tracked YAML/JSON file. Update an existing manifest
only if it contains a direct conflict with the reconciliation. Do not create a
manifest to demonstrate the future architecture.

Expected result: no manifest change. The repository currently has one production
skill and no declarative workflow manifest. Reconsider `skills-index.yaml` only
when approximately 3–5 production skills or multiple workflows establish a real
discovery or drift problem.

Search README, AGENTS, and docs for stale milestone, MCP-next, model-provider-next,
and `AgentRuntime` authority wording. Historical wording may remain inside dated
records. Correct forward-facing README/AGENTS and the new reconciliation only.

## 5. Preserve Historical Records

Confirm an empty diff for:

- `docs/superpowers/plans/2026-08-20-ai-market-research-agent-premarket-v0.1.md`
- `docs/superpowers/plans/2026-08-31-product-a-skill-workflow-contract-delta.md`
- `docs/superpowers/plans/2026-09-09-v04-slice-4b-replay-contracts.md`

Do not reword old plans to match the actual release sequence. Their historical
assumptions and approval gates remain part of the record.

## 6. Verify and Publish for Review

Run:

```bash
pytest
ruff check .
mypy src
git diff --check
```

Then run the historical-plan preservation diff, stale-roadmap searches,
`AgentRuntime` authority search, skill/manifest inventory, `git status --short`,
`git diff --stat`, and the complete `git diff`.

The final diff must contain only the four expected documentation files. Explain
any additional file before proceeding.

Create one focused branch and pull request:

- branch: `codex/docs-roadmap-reconciliation`
- title: `docs: reconcile Product A roadmap after v0.5`
- base: current `main`

The pull request must explain why the implementation sequence diverged from the
historical blueprint, what documentation was reconciled, what remains unchanged,
and the exact verification results. Push for human review and do not merge.
