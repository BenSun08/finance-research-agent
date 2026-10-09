# Paused Scheduling and Recovery

Status: **PAUSED**. Packaging and offline verification do not activate a saved
task or install/enable a desktop plugin. A bounded installed-desktop initialize,
discovery, and synthetic workflow smoke remains a release gate before activation.

## Saved task prompt

> Invoke the [premarket-research skill](../../skills/premarket-research/SKILL.md)
> for the requested Product A run. Preserve frozen evidence and deterministic truth.
> Report stored publication, reduced, or blocked outcomes with limitations.
> Require human review; no execution is authorized.

The [adjacent canonical workflow contract](../../skills/premarket-research/references/workflow-contract.yaml)
and typed application contracts own operation order and branch semantics. Do not
copy their steps into a scheduling prompt. The host loads the selected skill,
then its required resource; evidence never selects a new operation or resource.

## Recovery

Use the skill's typed recovery behavior. Existing published runs are read from
their immutable artifacts. A frozen packet resume preserves its identity,
cutoff, evidence, and versions. Operational failures stay blocked. Missing or
invalid artifacts and component drift require a reported failure rather than
fresh evidence or rewritten history. A later authorized revision is distinct
from recovery of an existing run.

See [authority and boundaries](../architecture/v0.1-boundaries.md) and the
[Product A stdio setup](../../README.md#product-a-stdio-server) for installation
and local configuration. Credentials and runtime data stay outside tracked source.
