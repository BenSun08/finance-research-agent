# Quickstart

This guide runs the existing research build, version `0.5.0.dev0`, on Python
3.12 or newer. The approved trading-agent target is described in [spec](spec.md);
there is no implemented paper-account or dashboard command yet. The first Work
integration must pass [RQ-01](research.md#rq-01-work-integration).

## Install the current build

Use a private environment in a checkout. The lock file and CI interpreter matrix
are the reproducible installation reference. With the desired Python selected:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
```

Installation can download dependencies. Existing runtime dependencies are listed
in [pyproject.toml](../pyproject.toml); documentation consolidation does not add
any dependency or change the version. Keep project metadata, package version and
this current-version statement aligned on future releases.

## Offline checks

From the configured development environment:

```bash
python -m finance_research_agent.schema_export --check
python -m scripts.check_docs_examples
FINANCE_RESEARCH_AGENT_OFFLINE_TESTS=1 PYTHONPATH=tests/network_guard:src:. python -m pytest
ruff check .
mypy src
```

Default pytest excludes live tests. The environment guard blocks outbound network
while retaining local subprocess/stdio infrastructure. For an isolated synthetic
host smoke, explicitly clear `ALPACA_API_KEY` and `ALPACA_API_SECRET` and set
`AI_MARKET_RESEARCH_DOTENV_PATH` to an empty owner-only file so credential fallback
cannot reach personal configuration. Use a disposable private data directory
containing copies of the five [example policies](../config/examples/watchlist.yaml)
and the other YAML examples. Those policies are fixtures, not live-source setup.

## Diagnostic CLI

The installed CLI supports these commands:

```bash
finance-research-agent status
finance-research-agent config
finance-research-agent replay RUN_ID
```

Replace `RUN_ID` with an existing published run. Status/config inspect local
readiness; they do not collect market data. Replay reads immutable artifacts,
checks versions/hashes and requires no provider credentials. There are no
`--offline`, `--smoke`, or `--data-dir` CLI flags. Configure the data root using
`AI_MARKET_RESEARCH_DATA_DIR` in the process environment. Use the installed
entrypoint; the CLI module is not a `python -m` executable.

## Product A stdio server

Canonical host instructions are indexed in [Project Skills](../skills/README.md).
See [current contracts](contracts.md#current-implementation-profile) and
[operations and recovery](operations.md).
The premarket [workflow contract](../skills/premarket-research/references/workflow-contract.yaml)
owns orchestration order and handoffs. Packaging does not enable a schedule or
install a plugin into a desktop host.

Launch the installed local server from an MCP host with command
`ai-market-research-mcp` and an empty argument list. For a source checkout, run:

```bash
PYTHONPATH=src python -m finance_research_agent.adapters.mcp_bootstrap
```

The server exposes the eleven typed Product A operations over stdin/stdout and
opens no listening socket. Initialization and tool listing need no credentials,
configuration files, Keychain access, provider requests or storage writes.
Stdout is reserved for MCP protocol messages; startup failures use a closed error
code on stderr.

Set `AI_MARKET_RESEARCH_DATA_DIR` in the server process environment to locate
local configuration and artifacts; the default is `data` relative to its working
directory. This locator does not read `.env`. Place the validated five policy
files under the chosen data directory's `config` folder. The example policies
are offline fixtures, and their `example.test` allowlist does not permit Alpaca
requests. Real requests require an operator-configured source policy allowing
the existing Alpaca adapter and its market-data/instrument-reference hosts.

Credentials are resolved only when provider readiness or collection needs them,
using the existing environment, macOS Keychain and owner-only `.env` sources.
Preparation constructs the real Alpaca provider from the run's frozen source
policy and trusted deadline. Frozen packet, quality-checkpoint and published-run
resumes preserve their stored evidence without current provider construction.
The current source scope remains Alpaca market data and the local market calendar;
missing event verification blocks actionable plans. Human review gates remain.

## First host use

Use the canonical [skill index](../skills/README.md) and its declared resources.
The current premarket skill is a research workflow with frozen evidence and
local validation. Preserve its existing behavior until the new Work interface
is designed and verified. Do not infer host installation from packaging tests.

For local diagnostics, recovery, opt-in live checks and release receipts, use
[operations](operations.md). For exact API examples use [contracts](contracts.md).
