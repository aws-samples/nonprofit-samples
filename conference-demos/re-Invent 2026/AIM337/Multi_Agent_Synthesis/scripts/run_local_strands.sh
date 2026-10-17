#!/usr/bin/env bash
# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"

python_bin="${project_root}/.venv/bin/python"
if [[ ! -x "${python_bin}" ]]; then
    printf '%s\n' \
        "error: ${python_bin} was not found." \
        "Create the project virtual environment and install .[aws,dev] first." >&2
    exit 2
fi

# Configuration is resolved entirely in Python (src/evidence_synthesis/config.py):
# explicit env vars win, then a .env file in the working directory, then code defaults.
print_config() {
    "${python_bin}" - <<'PY'
from evidence_synthesis.config import PipelinePolicy, StrandsSettings
from evidence_synthesis.models import AgentRole

settings = StrandsSettings()
policy = PipelinePolicy()
print(f"AWS_REGION={settings.region}")
for role in AgentRole:
    prefix = role.value.upper()
    print(f"{prefix}_MODEL_ID={settings.model_ids[role]}")
    print(f"{prefix}_MAX_TOKENS={settings.max_tokens[role]}")
print(f"PIPELINE_DEADLINE_SECONDS={policy.deadline_seconds}")
print(f"PIPELINE_TOKEN_BUDGET={policy.token_budget}")
print(f"PIPELINE_MAX_RETRIES={policy.max_retries}")
print(f"PIPELINE_MAX_VERIFICATION_CYCLES={policy.max_verification_cycles}")
PY
}

usage() {
    printf '%s\n' \
        "Usage:" \
        "  scripts/run_local_strands.sh --show-config" \
        "  scripts/run_local_strands.sh MANIFEST [run options]" \
        "" \
        "Examples:" \
        "  scripts/run_local_strands.sh /tmp/evidence.json" \
        "  scripts/run_local_strands.sh /tmp/evidence.json --output runs --json"
}

if [[ "${1:-}" == "--show-config" ]]; then
    print_config
    exit 0
fi

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

manifest="$1"
shift

if [[ ! -f "${manifest}" ]]; then
    printf 'error: manifest does not exist: %s\n' "${manifest}" >&2
    exit 2
fi

printf 'Running local Strands pipeline with:\n' >&2
print_config >&2

exec "${python_bin}" -m evidence_synthesis.cli run \
    --manifest "${manifest}" \
    --runtime strands \
    "$@"
