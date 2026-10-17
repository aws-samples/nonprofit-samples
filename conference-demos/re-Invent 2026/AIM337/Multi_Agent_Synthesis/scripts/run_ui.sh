#!/usr/bin/env bash
# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
streamlit_bin="${project_root}/.venv/bin/streamlit"

# Pipeline limits and model routing are resolved in Python
# (src/evidence_synthesis/config.py): env vars win, then .env, then code defaults.
export EVIDENCE_SYNTHESIS_REGION="${EVIDENCE_SYNTHESIS_REGION:-us-east-1}"

if [[ ! -x "${streamlit_bin}" ]]; then
    printf '%s\n' \
        "error: Streamlit is not installed in ${project_root}/.venv." \
        "Install the UI dependencies with: python -m pip install -e '.[aws,ui,dev]'" >&2
    exit 2
fi

cd "${project_root}"
exec "${streamlit_bin}" run \
    src/evidence_synthesis/ui/app.py \
    --server.address=127.0.0.1 \
    --server.headless=false
