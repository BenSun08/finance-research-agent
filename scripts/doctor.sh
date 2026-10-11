#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONDONTWRITEBYTECODE=1

python_bin=""
for candidate in python python3; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
        continue
    fi
    if "$candidate" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
        >/dev/null 2>&1; then
        python_bin="$(command -v "$candidate")"
        break
    fi
done

if [[ -z "$python_bin" ]]; then
    printf '%s\n' 'Supported Python: FAIL (activate the locked project environment)' >&2
    exit 1
fi

if [[ -n "${AI_MARKET_RESEARCH_DATA_DIR:-}" ]]; then
    data_root="$AI_MARKET_RESEARCH_DATA_DIR"
elif ! data_root="$("$python_bin" -c \
    'from finance_research_agent.settings import Settings; print(Settings.from_sources().data_dir.expanduser().resolve())' \
    2>/dev/null)"; then
    data_root=data
fi

if [[ -L "$data_root" || ! -d "$data_root" || ! -w "$data_root" ]]; then
    printf '%s\n' 'Data-root writable probe: FAIL (configure an existing writable private data root)' >&2
    exit 1
fi

probe="$(mktemp "$data_root/.doctor-write.XXXXXX")" || {
    printf '%s\n' 'Data-root writable probe: FAIL (configure an existing writable private data root)' >&2
    exit 1
}
trap 'rm -f "$probe"' EXIT
trap 'exit 1' HUP INT TERM
printf '%s\n' 'Data-root writable probe: PASS'

export AI_MARKET_RESEARCH_DATA_DIR="$data_root"
"$python_bin" -m scripts.doctor
